"""
InsightHub ChatOps Bot — structured audit log.

Mỗi quyết định quyền và mỗi tool call ghi 1 dòng JSON (JSONL) vào CHATOPS_AUDIT_LOG.
`decision` chỉ có 3 giá trị: allowed | denied | approval_required.
Kết quả thực thi nằm ở `outcome` (ok | error | expired | pending) để không trộn với quyết định quyền.
Không ghi token, secret, raw tool output; `result` là summary đã rút gọn.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DECISIONS = frozenset({"allowed", "denied", "approval_required"})
_lock = threading.Lock()
_MAX_RESULT = 500


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        user: str,
        action: str,
        decision: str,
        tier: str,
        tool: str | None = None,
        args: dict[str, Any] | None = None,
        result: str = "",
        outcome: str = "ok",
        slack_event_id: str | None = None,
        identity: str = "chatops-readonly",
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        if decision not in DECISIONS:
            raise ValueError(f"invalid decision {decision!r}")
        event: dict[str, Any] = {
            "timestamp": utc_now(),
            "event_id": uuid.uuid4().hex,
            "slack_event_id": slack_event_id,
            "user": user or "unknown",
            "action": action,
            "tier": tier,
            "tool": tool,
            "args": args or {},
            "decision": decision,
            "approved": decision == "allowed",
            "identity": identity,
            "outcome": outcome,
            "result": result[:_MAX_RESULT],
            "duration_ms": duration_ms,
        }
        run_id = os.environ.get("INSIGHTHUB_VERIFY_RUN_ID")
        if run_id:
            event["test_run_id"] = run_id
        line = json.dumps(event, ensure_ascii=False)
        with _lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return event

    def read_events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]


def log_tool_call(
    user: str,
    tool: str,
    args: dict,
    result_summary: str,
    approved: bool = True,
) -> None:
    """Giữ API cũ của skeleton; ghi vào log mặc định."""
    from .config import load_settings

    AuditLog(load_settings().audit_log).record(
        user=user, action=tool, tool=tool, args=args, result=result_summary,
        decision="allowed" if approved else "denied", tier="read",
    )
