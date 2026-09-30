"""InsightHub guardrail for the LiteLLM gateway (loaded via config.yaml).

Layered checks on every chat completion that passes through the gateway, for all
three workloads (insighthub / chatops-bot / coding):

  pre_call  L1 rules      direct injection, system-prompt extraction, secret/PII in input
            L1b context   InsightHub RAG payload: strip instruction-like lines in documents
            L2 Llama Guard 3 (Ollama) on the end-user text -> block unsafe categories
  post_call L3 output     redact PII/secrets, block system-prompt leakage / injection canary

GUARDRAIL_MODE=enforce (default) | monitor (log only) | off.
Blocks raise HTTP 400 with the fixed marker "insighthub_guardrail_blocked", which the
InsightHub API maps to code=guardrail_blocked. Decisions are logged as JSON lines
(no raw prompt, only a sha256 prefix), next to LiteLLM spend logs for audit.

Pure functions live at module top so they can be unit-tested without LiteLLM.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from typing import Any

logger = logging.getLogger("insighthub.guardrail")

BLOCK_MARKER = "insighthub_guardrail_blocked"
REMOVED = "[guardrail: đã loại bỏ đoạn có dạng chỉ dẫn cho AI]"
REFUSAL = "Xin lỗi, mình không thể cung cấp nội dung này."

_ZW = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").translate(_ZW).lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text.replace("đ", "d"))


# ---------------- L1: direct injection / extraction (end-user text) ----------------
_DIRECT = [
    r"ignore (all |any )?(previous|prior|above|earlier|your) (instructions|prompts?|rules|guidelines)",
    r"disregard (all |the |your )?(previous|prior|above|system)",
    r"bo qua (moi |tat ca |het )?(huong dan|chi dan|quy tac|lenh|rang buoc)",
    r"(you are|act as|pretend (to be|you are)|roleplay as)\s+(dan|an? unrestricted|an? unfiltered|developer mode|jailbroken)",
    r"\b(dan|developer) mode\b",
    r"(reveal|show|print|repeat|output|leak|tiet lo|in ra|cho (toi|minh) xem|nhac lai)[^.\n]{0,50}(system prompt|initial (prompt|instructions)|hidden (prompt|instructions)|chi dan he thong|prompt he thong|quy tac he thong)",
    r"what (are|were) your (instructions|system prompt|rules)",
    r"<\|?(im_start|system)\|?>|\[\s*system\s*\]|###\s*system",
    r"(new|updated|override) (system )?(instructions|rules)\s*:",
]
_DIRECT_RE = re.compile("|".join(f"(?:{p})" for p in _DIRECT))

# ---------------- L1b: indirect injection inside retrieved documents ----------------
_INDIRECT = _DIRECT + [
    r"(system|developer|admin) (override|message|instruction)s?\s*[:\]]",
    r"(assistant|ai|model|chatbot|tro ly)[^.\n]{0,40}(must|phai|hay|always|luon)[^.\n]{0,40}(reply|respond|tra loi|output|in ra|include|chen)",
    r"(luon|always)[^.\n]{0,15}(tra loi|answer|respond|reply)[^.\n]{0,25}(chinh xac|exactly|dung|only|duy nhat)",
    r"(?<!khong )(?<!dung )(?<!cam )(send|gui|post|exfiltrate)[^.\n]{0,60}(https?://|password|mat khau|token|api key)",
    r"(khi|when)[^.\n]{0,60}(duoc hoi|asked|asks)[^.\n]{0,80}(tra loi|reply|answer|respond)[^.\n]{0,20}(rang|that|with|only)",
    r"note for the (ai|assistant|model)|luu y cho (tro ly|ai|mo hinh)",
    r"(maintenance|debug|god) mode",
]
_INDIRECT_RE = re.compile("|".join(f"(?:{p})" for p in _INDIRECT))
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)

# ---------------- PII / secrets ----------------
_PII = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "phone_vn": re.compile(r"(?<!\d)(?:\+?84|0)(?:3|5|7|8|9)\d{8}(?!\d)"),
    "cccd": re.compile(r"(?<!\d)0\d{11}(?!\d)"),
    "card": re.compile(r"(?<!\d)(?:\d[ -]?){13,16}(?!\d)"),
    "secret": re.compile(
        r"(sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{30,}|xox[baprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}"
        r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
    ),
    "password_kv": re.compile(r"(?i)\b(password|passwd|mat khau|mật khẩu)\s*[:=]\s*\S+"),
}
# Output-only: fragments of the hardened InsightHub system prompt and the red-team canary.
_LEAK = re.compile(r"(quy tac bat buoc, uu tien cao hon|ih-canary-\d+|khong tiet lo, tom tat hay dien giai)")


def detect_direct_injection(text: str) -> str | None:
    m = _DIRECT_RE.search(normalize(text))
    return m.group(0)[:60] if m else None


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n\s*\n+")


def strip_indirect(text: str) -> tuple[str, int]:
    # Sentence granularity, not paragraph: the app-side chunker (api/app/services/
    # chunking.py) rejoins each chunk with `" ".join(text.split())`, so a chunk reaching
    # the gateway has no blank line to split on -- paragraph splitting degenerated to
    # "the whole chunk is one paragraph" and wiped benign facts sharing a chunk with an
    # injected block. Mirrors the same fix in api/app/services/sanitize.py.
    #
    # HTML-comment removal must happen PER SENTENCE, not once over the whole text before
    # splitting: substituting the comment span with the REMOVED marker (which carries no
    # sentence-ending punctuation) used to erase the sentence boundary right after it, so
    # the split regex fused the comment's neighboring sentence into the same chunk as the
    # injected text before it -- and legitimate content past the comment got stripped too.
    out = []
    removed = 0
    prev_removed = False
    for sentence in _SENTENCE_SPLIT.split((text or "").translate(_ZW)):
        if not sentence:
            continue
        stripped, comment_hits = _HTML_COMMENT.subn("", sentence)
        is_indirect = bool(comment_hits) or bool(_INDIRECT_RE.search(normalize(stripped)))
        if is_indirect:
            removed += 1
            if not prev_removed:
                out.append(REMOVED)
            prev_removed = True
        else:
            out.append(sentence)
            prev_removed = False
    return " ".join(out), removed


def redact_pii(text: str) -> tuple[str, list[str]]:
    found: list[str] = []
    for kind, rx in _PII.items():
        if rx.search(text):
            found.append(kind)
            text = rx.sub(f"[đã ẩn:{kind}]", text)
    return text, found


def detect_leak(text: str) -> bool:
    return bool(_LEAK.search(normalize(text)))


def split_insighthub_payload(content: str) -> tuple[dict | None, str]:
    """InsightHub sends {"documents":[...], "question": ...} as the user message."""
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return None, content
    if isinstance(data, dict) and isinstance(data.get("documents"), list) and isinstance(data.get("question"), str):
        return data, data["question"]
    return None, content


def parse_llama_guard(output: str) -> tuple[bool, str]:
    lines = [x.strip() for x in (output or "").strip().splitlines() if x.strip()]
    if not lines:
        raise ValueError("empty llama guard output")
    if lines[0].lower() == "safe":
        return True, ""
    if lines[0].lower() == "unsafe":
        return False, (lines[1] if len(lines) > 1 else "unknown")
    raise ValueError("unexpected llama guard output")


# Llama Guard 3 hazard categories we block (S1-S14). S7 privacy + S14 code-interpreter abuse included.
BLOCK_CATEGORIES = {f"S{i}" for i in range(1, 15)}


def _audit(**fields: Any) -> None:
    fields.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    logger.warning("GUARDRAIL %s", json.dumps(fields, ensure_ascii=False))


def _digest(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:12]


# ---------------- LiteLLM integration ----------------
try:  # pragma: no cover - exercised inside the gateway image
    import httpx
    from fastapi import HTTPException
    from litellm.integrations.custom_guardrail import CustomGuardrail
except ImportError:  # unit tests without litellm
    CustomGuardrail = object  # type: ignore[assignment,misc]
    HTTPException = None  # type: ignore[assignment]
    httpx = None  # type: ignore[assignment]


class InsightHubGuardrail(CustomGuardrail):  # type: ignore[misc,valid-type]
    def __init__(self, **kwargs: Any) -> None:
        self.mode = os.environ.get("GUARDRAIL_MODE", "enforce").lower()
        self.ollama = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434").rstrip("/")
        self.guard_model = os.environ.get("LLAMA_GUARD_MODEL", "llama-guard3:1b")
        self.guard_enabled = os.environ.get("LLAMA_GUARD_ENABLED", "true").lower() == "true"
        self.fail_closed = os.environ.get("LLAMA_GUARD_FAIL", "closed").lower() == "closed"
        # Cold load of the guard model on a busy Ollama (grader/qwen running) takes >8s; a short
        # timeout aborted every request -> fail-closed blocked 100% of traffic. keep_alive keeps the
        # model resident so only the first call after an idle period pays the load.
        self.guard_timeout = float(os.environ.get("LLAMA_GUARD_TIMEOUT", "30"))
        self.guard_keep_alive = os.environ.get("LLAMA_GUARD_KEEP_ALIVE", "60m")
        # Code-review profile: diffs legitimately contain injection-looking strings (e.g. this
        # guardrail's own regexes or the hardened prompt) and fake keys in test fixtures, so
        # for these keys secrets are redacted in place instead of blocking, and the
        # direct-injection regex is skipped; Llama Guard still runs.
        self.code_keys = {k.strip() for k in os.environ.get("GUARDRAIL_CODE_KEYS", "coding").split(",") if k.strip()}
        if CustomGuardrail is not object:
            super().__init__(**kwargs)

    # -- helpers --
    def _block(self, key: str, layer: str, reason: str, text: str) -> None:
        _audit(decision="blocked" if self.mode == "enforce" else "would_block", key=key, layer=layer,
               reason=reason, input_sha=_digest(text), mode=self.mode)
        if self.mode == "enforce":
            raise HTTPException(status_code=400, detail={"error": BLOCK_MARKER, "layer": layer, "reason": reason})

    async def _llama_guard(self, text: str) -> tuple[bool, str]:
        async with httpx.AsyncClient(timeout=self.guard_timeout, trust_env=False) as client:
            r = await client.post(f"{self.ollama}/api/chat", json={
                "model": self.guard_model, "stream": False, "keep_alive": self.guard_keep_alive,
                "messages": [{"role": "user", "content": text[:4000]}],
            })
            r.raise_for_status()
            return parse_llama_guard(r.json()["message"]["content"])

    # -- hooks --
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type):  # noqa: ANN001
        if self.mode == "off" or call_type not in {"completion", "acompletion", "text_completion", "anthropic_messages"}:
            return data
        key = getattr(user_api_key_dict, "key_alias", None) or "unknown"
        messages = data.get("messages") or []
        user_texts: list[str] = []
        for msg in messages:
            if msg.get("role") != "user" or not isinstance(msg.get("content"), str):
                continue
            payload, question = split_insighthub_payload(msg["content"])
            if payload is not None:
                stripped = 0
                for doc in payload["documents"]:
                    if isinstance(doc, dict) and isinstance(doc.get("text"), str):
                        doc["text"], n = strip_indirect(doc["text"])
                        stripped += n
                if stripped:
                    _audit(decision="sanitized", key=key, layer="L1b-context", removed=stripped, mode=self.mode)
                    if self.mode == "enforce":
                        msg["content"] = json.dumps(payload, ensure_ascii=False)
            if key in self.code_keys:
                redacted, kinds = redact_pii(msg["content"])
                if kinds:
                    _audit(decision="input_redacted", key=key, layer="L1-code-profile", kinds=kinds, mode=self.mode)
                    if self.mode == "enforce":
                        msg["content"] = redacted
                        question = redacted if payload is None else question
            user_texts.append(question)
        # Only the latest end-user turn is classified (tool results / history are data).
        text = user_texts[-1] if user_texts else ""
        if not text:
            return data
        if key not in self.code_keys:
            hit = detect_direct_injection(text)
            if hit:
                self._block(key, "L1-rules", f"direct_injection:{hit}", text)
            _, pii = redact_pii(text)
            if "secret" in pii or "card" in pii or "cccd" in pii:
                self._block(key, "L1-rules", f"sensitive_input:{','.join(pii)}", text)
        if self.guard_enabled:
            try:
                safe, category = await self._llama_guard(text)
            except Exception as exc:  # noqa: BLE001
                _audit(decision="guard_error", key=key, layer="L2-llama-guard", error=type(exc).__name__,
                       fail_closed=self.fail_closed)
                if self.fail_closed:
                    self._block(key, "L2-llama-guard", "guard_unavailable", text)
            else:
                if not safe and category.split(",")[0] in BLOCK_CATEGORIES:
                    self._block(key, "L2-llama-guard", f"unsafe:{category}", text)
        _audit(decision="allowed", key=key, layer="pre_call", input_sha=_digest(text), mode=self.mode)
        return data

    async def async_post_call_success_hook(self, data: dict, user_api_key_dict, response):  # noqa: ANN001
        if self.mode == "off":
            return response
        key = getattr(user_api_key_dict, "key_alias", None) or "unknown"
        for choice in getattr(response, "choices", []) or []:
            message = getattr(choice, "message", None)
            content = getattr(message, "content", None)
            if not isinstance(content, str):
                continue
            if detect_leak(content):
                _audit(decision="output_blocked", key=key, layer="L3-output", reason="prompt_or_canary_leak",
                       mode=self.mode)
                if self.mode == "enforce":
                    message.content = REFUSAL
                continue
            redacted, found = redact_pii(content)
            if found:
                _audit(decision="output_redacted", key=key, layer="L3-output", kinds=found, mode=self.mode)
                if self.mode == "enforce":
                    message.content = redacted
        return response
