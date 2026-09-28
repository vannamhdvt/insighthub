"""3-tier permission + approval token.

Tier 1 read        -> tự động cho phép (identity chatops-readonly qua MCP --read-only).
Tier 2 write       -> chỉ operator được yêu cầu; luôn `approval_required`, token 60s, dùng 1 lần,
                      bind với đúng action + args + channel. Thực thi bằng identity riêng chatops-scaler.
Tier 3 destructive -> bot từ chối; làm ngoài bot theo break-glass runbook.

Quyền được enforce bằng code + RBAC K8s, không bằng prompt.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .intents import Intent

READ, WRITE, DESTRUCTIVE = "read", "write", "destructive"

ACTION_TIERS: dict[str, str] = {
    "help": READ,
    "health": READ,
    "ingest_today": READ,
    "failing_pods": READ,
    "llm_query": READ,
    "unknown": READ,
    "scale": WRITE,
    "confirm": WRITE,
    "cancel": WRITE,
    "destructive": DESTRUCTIVE,
}

# Scale allowlist sinh từ service-catalog.yaml: chỉ service `scalable: true`, trong [min, max].
@dataclass(frozen=True)
class ScaleTarget:
    deployment: str
    min_replicas: int
    max_replicas: int


def load_scale_targets(catalog: Path) -> dict[str, ScaleTarget]:
    data = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    targets: dict[str, ScaleTarget] = {}
    for svc in data.get("services", []):
        if not svc.get("scalable"):
            continue
        bounds = svc.get("replicas") or {}
        target = ScaleTarget(svc["name"], int(bounds.get("min", 1)), int(bounds.get("max", 1)))
        for key in {svc["name"], svc.get("alias"), svc["name"].removeprefix("insighthub-")} - {None}:
            targets[key] = target
    return targets


@dataclass(frozen=True)
class Decision:
    decision: str  # allowed | denied | approval_required
    tier: str
    reason: str
    action: str
    args: dict[str, Any] = field(default_factory=dict)


class PermissionPolicy:
    def __init__(self, operator_ids: frozenset[str], targets: dict[str, ScaleTarget]) -> None:
        self.operator_ids = operator_ids
        self.targets = targets

    def is_operator(self, user: str) -> bool:
        return user in self.operator_ids

    def decide(self, user: str, intent: Intent) -> Decision:
        tier = ACTION_TIERS.get(intent.name, DESTRUCTIVE)  # action lạ = nguy hiểm nhất
        if tier == READ:
            return Decision("allowed", tier, "read-only", intent.name, dict(intent.params))
        if tier == DESTRUCTIVE:
            return Decision("denied", tier, "destructive action is never executed from chat", intent.name,
                            dict(intent.params))
        if not self.is_operator(user):
            return Decision("denied", tier, "user is not a ChatOps operator", intent.name, dict(intent.params))
        if intent.name in {"confirm", "cancel"}:
            return Decision("allowed", tier, "operator may answer an approval", intent.name, dict(intent.params))
        target = self.targets.get(str(intent.params.get("target", "")))
        replicas = intent.params.get("replicas")
        if target is None:
            return Decision("denied", tier, "deployment not in scale allowlist", "scale", dict(intent.params))
        if not isinstance(replicas, int) or not target.min_replicas <= replicas <= target.max_replicas:
            return Decision("denied", tier, f"replicas must be {target.min_replicas}-{target.max_replicas}",
                            "scale", dict(intent.params))
        return Decision("approval_required", tier, "write action needs confirmation", "scale",
                        {"deployment": target.deployment, "replicas": replicas})


class ApprovalError(Exception):
    pass


@dataclass
class PendingApproval:
    token: str
    action: str
    args: dict[str, Any]
    requester: str
    channel: str
    binding: str
    expires_at: float
    used: bool = False


def action_binding(action: str, args: dict[str, Any], channel: str) -> str:
    raw = json.dumps({"action": action, "args": args, "channel": channel}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


class ApprovalStore:
    """Token ngắn, 1 lần, hết hạn theo epoch UTC (time.time) để không lệch time zone."""

    def __init__(self, ttl_seconds: int = 60, clock=time.time) -> None:
        self.ttl = ttl_seconds
        self.clock = clock
        self._items: dict[str, PendingApproval] = {}
        self._lock = threading.Lock()

    def issue(self, action: str, args: dict[str, Any], requester: str, channel: str) -> PendingApproval:
        token = secrets.token_hex(4)
        item = PendingApproval(token, action, dict(args), requester, channel,
                               action_binding(action, args, channel), self.clock() + self.ttl)
        with self._lock:
            self._items[token] = item
        return item

    def peek(self, token: str) -> PendingApproval | None:
        with self._lock:
            return self._items.get(token)

    def redeem(self, token: str, approver: str, channel: str, action: str, args: dict[str, Any]) -> PendingApproval:
        """Tiêu thụ token. `action/args` là thứ SẮP thực thi; phải khớp đúng thứ đã được duyệt."""
        with self._lock:
            item = self._items.get(token)
            if item is None:
                raise ApprovalError("unknown token")
            if item.used:
                raise ApprovalError("token already used")
            if self.clock() > item.expires_at:
                item.used = True
                raise ApprovalError("token expired")
            if item.channel != channel:
                raise ApprovalError("token belongs to another channel")
            if action_binding(action, args, channel) != item.binding:
                raise ApprovalError("token is bound to a different action")
            item.used = True
            return item

    def cancel(self, token: str) -> bool:
        with self._lock:
            item = self._items.get(token)
            if item is None or item.used:
                return False
            item.used = True
            return True
