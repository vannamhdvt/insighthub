"""Rule-based intent router: chạy không cần LLM, deterministic, dễ test.

LLM (nếu bật) chỉ xử lý câu `unknown`, và chỉ với tool read-only.
Mutation (scale) KHÔNG bao giờ được suy ra từ LLM: phải khớp regex ở đây.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

_MENTION = re.compile(r"<@[A-Z0-9]+>")


def normalize(text: str) -> str:
    text = _MENTION.sub(" ", text or "")
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn").replace("đ", "d")
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class Intent:
    name: str
    params: dict[str, Any] = field(default_factory=dict)


_SCALE = re.compile(r"\bscale\s+(?:deploy(?:ment)?/)?([a-z0-9-]+)\s+(?:to\s+|len\s+|=\s*)?(-?\d+)\b")
_CONFIRM = re.compile(r"\b(?:confirm|approve|xac nhan)\s+([a-z0-9]{6,32})\b")
_CANCEL = re.compile(r"\b(?:cancel|huy)\s+([a-z0-9]{6,32})\b")
_DESTRUCTIVE = re.compile(
    r"\b(delete|xoa|drain|cordon|rollback|rollout\s+(?:undo|restart)|exec|evict|kill|truncate|drop|uninstall)\b"
)
_HEALTH = re.compile(r"\b(healthy|health|khoe|on khong|ok khong|status|trang thai|up khong|song khong)\b")
_INGEST = re.compile(r"\b(ingest\w*|ingestion|upload\w*|tai lieu|document\w*|doc)\b")
_COUNT = re.compile(r"\b(bao nhieu|how many|count|so luong|today|hom nay|tong)\b")
_PODS = re.compile(r"\b(pods?)\b")
_FAILING = re.compile(r"\b(loi|fail\w*|crash\w*|error\w*|hong|restart\w*|not ready|pending|chet)\b")
_HELP = re.compile(r"^(help|giup|\?|huong dan)$")


def route(text: str) -> Intent:
    t = normalize(text)
    if not t or _HELP.match(t):
        return Intent("help")
    if m := _CONFIRM.search(t):
        return Intent("confirm", {"token": m.group(1)})
    if m := _CANCEL.search(t):
        return Intent("cancel", {"token": m.group(1)})
    if m := _SCALE.search(t):
        return Intent("scale", {"target": m.group(1), "replicas": int(m.group(2))})
    if m := _DESTRUCTIVE.search(t):
        return Intent("destructive", {"verb": m.group(1)})
    if _PODS.search(t) and _FAILING.search(t):
        return Intent("failing_pods")
    if _INGEST.search(t) and _COUNT.search(t):
        return Intent("ingest_today")
    if _HEALTH.search(t):
        return Intent("health")
    return Intent("unknown", {"text": t[:300]})
