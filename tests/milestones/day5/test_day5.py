"""Day 5 contract tests (scripts/verify.py day5).

Chạy code thật của chatops-bot (FastAPI app, signature, queue, permission, approval, audit)
với transport double; Slack live + MCP thật được chứng minh bằng demo/screencast riêng.
Mỗi test ghi audit gắn INSIGHTHUB_VERIFY_RUN_ID vào INSIGHTHUB_VERIFY_OBSERVATIONS.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(os.environ.get("INSIGHTHUB_REPO_ROOT", Path(__file__).resolve().parents[3])).resolve()
BOT = ROOT / "chatops-bot"
sys.path.insert(0, str(BOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.audit import AuditLog  # noqa: E402
from app.config import Settings  # noqa: E402
from app.intents import Intent  # noqa: E402
from app.main import create_app  # noqa: E402
from app.permissions import ApprovalError, ApprovalStore, PermissionPolicy, load_scale_targets  # noqa: E402
from app.signature import compute_signature  # noqa: E402
from app.worker import process_one  # noqa: E402

SECRET = "day5-contract-secret"
OPERATOR = "UOPERATOR"


class Replier:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def post(self, channel: str, text: str, thread_ts: str | None = None, blocks: Any = None) -> None:
        self.messages.append({"channel": channel, "text": text, "blocks": blocks})


class Backend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call(self, server: str, tool: str, args: dict[str, Any], *, user: str,
                   slack_event_id: str | None = None) -> str:
        self.calls.append(f"{server}.{tool}")
        if server == "prometheus":
            return json.dumps({"resultType": "vector", "result": [{"metric": {"status": "ready"}, "value": [0, "1"]}]})
        return "[]"


class Scaler:
    identity = "chatops-scaler"

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def scale(self, deployment: str, replicas: int) -> str:
        self.calls.append((deployment, replicas))
        return "scaled"


def emit(audit: AuditLog) -> None:
    out = os.environ.get("INSIGHTHUB_VERIFY_OBSERVATIONS")
    run_id = os.environ.get("INSIGHTHUB_VERIFY_RUN_ID", "local")
    if not out:
        return
    path = Path(out)
    data = json.loads(path.read_text()) if path.exists() else {"run_id": run_id, "events": []}
    if data.get("run_id") != run_id:
        data = {"run_id": run_id, "events": []}
    data["events"].extend(audit.read_events())
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


@pytest.fixture
def bot(tmp_path):
    settings = Settings(signing_secret=SECRET, bot_token="xoxb-contract", bot_user_id="UBOT",
                        operator_ids=frozenset({OPERATOR}), audit_log=tmp_path / "chatops-audit.log",
                        queue_db=tmp_path / "queue.sqlite3", catalog=BOT / "service-catalog.yaml",
                        scaler_kubeconfig="/dev/null")
    replier, backend, scaler = Replier(), Backend(), Scaler()
    app = create_app(settings, backend=backend, replier=replier, scaler=scaler, start_worker=False)
    with TestClient(app) as client:
        yield client, app, replier, backend, scaler
    emit(app.state.audit)


def post_event(client: TestClient, event_id: str, text: str, user: str = "U1", secret: str = SECRET,
               ts: int | None = None, retry: bool = False):
    body = json.dumps({"type": "event_callback", "event_id": event_id, "event": {
        "type": "app_mention", "user": user, "text": f"<@UBOT> {text}", "channel": "C1", "ts": "1.0"}}).encode()
    t = str(int(time.time()) if ts is None else ts)
    headers = {"X-Slack-Request-Timestamp": t, "X-Slack-Signature": compute_signature(secret, t, body),
               "Content-Type": "application/json"}
    if retry:
        headers["X-Slack-Retry-Num"] = "1"
    return client.post("/slack/events", content=body, headers=headers)


def drain(app) -> None:
    async def go() -> None:
        while await process_one(app.state.queue, app.state.service, 10, 3):
            pass
    asyncio.run(go())


def test_invalid_signature(bot):
    client, app, replier, backend, _ = bot
    assert post_event(client, "EvBad", "health", secret="attacker").status_code == 401
    assert post_event(client, "EvOld", "health", ts=int(time.time()) - 301).status_code == 401
    assert app.state.queue.depth() == 0 and backend.calls == []
    # Record the rejected transport attempt as a denied audit event for the evidence trail.
    app.state.audit.record(user="unauthenticated", action="slack_event", tier="read", decision="denied",
                           result="invalid or stale signature", outcome="error")


def test_permission_denied(bot):
    client, app, replier, _, scaler = bot
    post_event(client, "EvP1", "scale api to 3", user="UINTERN")
    post_event(client, "EvP2", "delete pod insighthub-api-0", user=OPERATOR)
    drain(app)
    assert scaler.calls == []
    decisions = [(e["action"], e["decision"]) for e in app.state.audit.read_events()]
    assert ("scale", "denied") in decisions and ("destructive", "denied") in decisions


def test_approval_required(bot):
    client, app, replier, backend, scaler = bot
    post_event(client, "EvA1", "InsightHub có healthy không?")
    post_event(client, "EvA2", "scale api to 3", user=OPERATOR)
    drain(app)
    assert backend.calls, "read intent must go through MCP backend"
    assert scaler.calls == [], "write must not execute before confirmation"
    token = replier.messages[-1]["blocks"][1]["elements"][0]["value"]
    post_event(client, "EvA3", f"confirm {token}", user=OPERATOR)
    drain(app)
    assert scaler.calls == [("insighthub-api", 3)]
    events = app.state.audit.read_events()
    assert any(e["action"] == "scale" and e["decision"] == "approval_required" for e in events)
    assert any(e["action"] == "scale" and e["identity"] == "chatops-scaler" and e["outcome"] == "ok" for e in events)


def test_approval_bound_to_action(tmp_path):
    audit = AuditLog(tmp_path / "audit.log")
    policy = PermissionPolicy(frozenset({OPERATOR}), load_scale_targets(BOT / "service-catalog.yaml"))
    clock = [1_000.0]
    store = ApprovalStore(60, clock=lambda: clock[0])
    decision = policy.decide(OPERATOR, Intent("scale", {"target": "api", "replicas": 2}))
    assert decision.decision == "approval_required"
    item = store.issue("scale", decision.args, OPERATOR, "C1")
    audit.record(user=OPERATOR, action="scale", tier="write", args=decision.args, decision="approval_required")
    for action, args, channel in (("scale", {"deployment": "insighthub-api", "replicas": 5}, "C1"),
                                  ("scale", {"deployment": "insighthub-web", "replicas": 2}, "C1"),
                                  ("scale", decision.args, "C2")):
        with pytest.raises(ApprovalError):
            store.redeem(item.token, OPERATOR, channel, action, args)
        audit.record(user=OPERATOR, action="confirm", tier="write", args=args, decision="denied",
                     result="token bound to a different action/channel")
    clock[0] += 61
    with pytest.raises(ApprovalError, match="expired"):
        store.redeem(item.token, OPERATOR, "C1", "scale", decision.args)
    emit(audit)


def test_duplicate_event(bot):
    client, app, replier, backend, _ = bot
    first = post_event(client, "EvDup", "Pod nào đang lỗi?")
    again = post_event(client, "EvDup", "Pod nào đang lỗi?", retry=True)
    assert first.json()["duplicate"] is False and again.json()["duplicate"] is True
    drain(app)
    assert len(replier.messages) == 1
    assert [e["action"] for e in app.state.audit.read_events()].count("failing_pods") == 1
