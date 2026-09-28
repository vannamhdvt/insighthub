"""Slack Web API adapter (slack_sdk). Tách qua Protocol để unit test dùng transport double."""
from __future__ import annotations

import asyncio
from typing import Any, Protocol


class Replier(Protocol):
    async def post(self, channel: str, text: str, thread_ts: str | None = None,
                   blocks: list[dict[str, Any]] | None = None) -> None: ...


class SlackReplier:
    def __init__(self, token: str) -> None:
        from slack_sdk import WebClient

        self._client = WebClient(token=token, timeout=10)

    async def post(self, channel: str, text: str, thread_ts: str | None = None,
                   blocks: list[dict[str, Any]] | None = None) -> None:
        kwargs: dict[str, Any] = {"channel": channel, "text": text, "thread_ts": thread_ts}
        if blocks:
            kwargs["blocks"] = blocks
        # WebClient là sync (urllib); chạy trong thread để không chặn event loop.
        await asyncio.to_thread(self._client.chat_postMessage, **kwargs)


def approval_blocks(text: str, token: str) -> list[dict[str, Any]]:
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text}},
        {"type": "actions", "elements": [
            {"type": "button", "style": "primary", "action_id": "chatops_approve", "value": token,
             "text": {"type": "plain_text", "text": "Approve"}},
            {"type": "button", "style": "danger", "action_id": "chatops_cancel", "value": token,
             "text": {"type": "plain_text", "text": "Cancel"}},
        ]},
    ]
