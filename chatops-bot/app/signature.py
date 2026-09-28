"""Slack HTTP request signing (v0) verification.

Chạy TRƯỚC mọi thứ khác, kể cả url_verification challenge: đọc raw body bytes,
không parse JSON trước khi verify (parse rồi serialize lại sẽ lệch chữ ký).
https://api.slack.com/authentication/verifying-requests-from-slack
"""
from __future__ import annotations

import hashlib
import hmac
import time


class SignatureError(Exception):
    """Request không chứng minh được là từ Slack. Luôn map sang HTTP 401."""


def compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    base = b"v0:" + timestamp.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def verify_slack_signature(
    secret: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    max_age: int = 300,
    now: float | None = None,
) -> None:
    if not secret:
        # Fail closed: thiếu secret thì không nhận request nào.
        raise SignatureError("signing secret not configured")
    if not timestamp or not signature:
        raise SignatureError("missing signature headers")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise SignatureError("invalid timestamp") from exc
    current = time.time() if now is None else now
    # Replay defense: từ chối request cũ hơn max_age (<= 5 phút) và cả timestamp tương lai.
    if abs(current - ts) > max_age:
        raise SignatureError("stale timestamp")
    expected = compute_signature(secret, timestamp, body)
    if not hmac.compare_digest(expected, signature):
        raise SignatureError("signature mismatch")
