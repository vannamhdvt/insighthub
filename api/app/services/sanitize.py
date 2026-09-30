"""Context sanitization: retrieved chunks are untrusted data (indirect prompt injection).

Layer 2 of defense in depth (after ingestion, before the LLM). Lines that look like
instructions aimed at the model are replaced with a fixed marker instead of being
forwarded. This is a heuristic filter, not a proof of safety; the gateway guardrail
and the hardened system prompt are separate layers.
"""

import re
import unicodedata

REMOVED_MARKER = "[đã loại bỏ đoạn có dạng chỉ dẫn cho AI]"

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)

# Normalized (lowercase, no accents) patterns of text addressed to the model.
_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above|earlier) (instructions|prompts?|rules)",
    r"disregard (all |the )?(previous|prior|above|system)",
    r"bo qua (moi |tat ca )?(huong dan|chi dan|quy tac|lenh)( truoc| o tren)?",
    r"(system|developer|admin) (override|prompt|message|instruction)s?\s*[:\]]",
    r"\[\s*(system|inst|assistant)\s*\]",
    r"<\|?(im_start|im_end|system|assistant)\|?>",
    r"you (are|must) now",
    r"(new|updated) instructions?\s*:",
    r"(assistant|ai|model|chatbot|tro ly)[^.\n]{0,40}(must|phai|hay|always|luon)[^.\n]{0,40}(reply|respond|tra loi|output|in ra|include|chen)",
    r"(luon|always)[^.\n]{0,15}(tra loi|answer|respond|reply)[^.\n]{0,25}(chinh xac|exactly|dung|only|duy nhat)",
    r"(reveal|print|repeat|leak|tiet lo|in ra)[^.\n]{0,40}(system prompt|instructions|chi dan he thong)",
    r"(?<!khong )(?<!dung )(?<!cam )(send|gui|post|exfiltrate)[^.\n]{0,60}(https?://|password|mat khau|token|api key)",
    r"(khi|when)[^.\n]{0,60}(duoc hoi|asked|asks)[^.\n]{0,80}(tra loi|reply|answer|respond)[^.\n]{0,20}(rang|that|with|only)",
    r"note for the (ai|assistant|model)|luu y cho (tro ly|ai|mo hinh)",
    r"(maintenance|developer|debug|god) mode",
]
_REGEX = re.compile("|".join(f"(?:{p})" for p in _PATTERNS))


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text.replace("đ", "d"))


def looks_like_instruction(text: str) -> bool:
    return bool(_REGEX.search(_normalize(text.translate(_ZERO_WIDTH))))


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n\s*\n+")


def sanitize_context(text: str) -> tuple[str, int]:
    """Return (clean_text, removed_segment_count)."""
    text = text.translate(_ZERO_WIDTH)
    # Sentence granularity, not paragraph: chunking.py (api/app/services/chunking.py)
    # rejoins the source with `" ".join(text.split())`, so a retrieved chunk never has a
    # blank line to split on -- splitting on blank lines degenerated to "the whole chunk
    # is one paragraph", wiping unrelated facts that shared a chunk with an injected block
    # (bug found via eval case benign-05 / inj-indirect-03 losing "10 MB" / supported
    # formats from huong-dan-nguoi-moi.md). An injected instruction still spans several
    # sentences ("NOTE FOR THE AI ... / You are now ... / ... reveal ..."), so consecutive
    # flagged sentences are coalesced into a single marker.
    #
    # HTML-comment removal must happen PER SENTENCE, not once over the whole text before
    # splitting: substituting the comment span with REMOVED_MARKER (no sentence-ending
    # punctuation) used to erase the sentence boundary right after it, fusing the comment's
    # neighboring sentence into the same chunk as the injected text before it -- so
    # legitimate content past the comment got wiped too.
    out = []
    removed = 0
    prev_removed = False
    for sentence in _SENTENCE_SPLIT.split(text):
        if not sentence:
            continue
        stripped, comment_hits = _HTML_COMMENT.subn("", sentence)
        flagged = bool(comment_hits) or looks_like_instruction(stripped)
        if flagged:
            removed += 1
            if not prev_removed:
                out.append(REMOVED_MARKER)
            prev_removed = True
        else:
            out.append(sentence)
            prev_removed = False
    return " ".join(out), removed
