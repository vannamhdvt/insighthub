import asyncio
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.signature import compute_signature
from app.worker import process_one
from conftest import OPERATOR, SECRET
from doubles import FakeBackend, FakeReplier, FakeScaler


def signed(body: bytes, ts: int | None = None, secret: str = SECRET) -> dict:
    t = str(int(time.time()) if ts is None else ts)
    return {"X-Slack-Request-Timestamp": t, "X-Slack-Signature": compute_signature(secret, t, body),
            "Content-Type": "application/json"}


def event(eid: str, text: str, user: str = "U1", **extra) -> bytes:
    ev = {"type": "app_mention", "user": user, "text": f"<@UBOT> {text}", "channel": "C1", "ts": "1.1", **extra}
    return json.dumps({"type": "event_callback", "event_id": eid, "event": ev}).encode()


@pytest.fixture
def bot(settings):
    replier, scaler, backend = FakeReplier(), FakeScaler(), FakeBackend()
    app = create_app(settings, backend=backend, replier=replier, scaler=scaler, start_worker=False)
    with TestClient(app) as client:
        yield client, app, replier, scaler, backend


def drain(app, settings):
    async def go():
        while await process_one(app.state.queue, app.state.service, 10, settings.worker_max_attempts):
            pass
    asyncio.run(go())


def test_invalid_signature_401_before_challenge(bot):
    client, *_ = bot
    body = json.dumps({"type": "url_verification", "challenge": "c"}).encode()
    r = client.post("/slack/events", content=body, headers=signed(body, secret="wrong"))
    assert r.status_code == 401 and "c" not in r.text


def test_stale_timestamp_401(bot):
    client, *_ = bot
    body = event("Ev1", "health")
    assert client.post("/slack/events", content=body, headers=signed(body, int(time.time()) - 400)).status_code == 401


def test_url_verification_after_valid_signature(bot):
    client, *_ = bot
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    assert client.post("/slack/events", content=body, headers=signed(body)).json() == {"challenge": "abc"}


def test_ack_is_fast_and_reply_comes_from_worker(bot, settings):
    client, app, replier, _, backend = bot
    body = event("Ev1", "InsightHub có healthy không?")
    started = time.monotonic()
    r = client.post("/slack/events", content=body, headers=signed(body))
    assert r.status_code == 200 and time.monotonic() - started < 3
    assert replier.messages == [] and backend.calls == []  # ACK không chờ MCP
    drain(app, settings)
    assert "healthy" in replier.messages[0]["text"] and replier.messages[0]["thread_ts"] == "1.1"


def test_slack_retry_is_deduplicated(bot, settings):
    client, app, replier, *_ = bot
    body = event("Ev2", "Pod nào đang lỗi?")
    h = signed(body)
    assert client.post("/slack/events", content=body, headers=h).json()["duplicate"] is False
    h2 = {**h, "X-Slack-Retry-Num": "1", "X-Slack-Retry-Reason": "http_timeout"}
    assert client.post("/slack/events", content=body, headers=h2).json()["duplicate"] is True
    drain(app, settings)
    assert len(replier.messages) == 1


def test_bot_ignores_its_own_messages(bot, settings):
    client, app, replier, *_ = bot
    for eid, extra in (("Ev3", {"user": "UBOT"}), ("Ev4", {"bot_id": "B1"})):
        body = event(eid, "health", **extra)
        client.post("/slack/events", content=body, headers=signed(body))
    drain(app, settings)
    assert replier.messages == []


def test_three_intents_audited_with_mcp_calls(bot, settings):
    client, app, replier, *_ = bot
    for i, q in enumerate(["api healthy?", "ingest count today?", "which pods failing?"]):
        body = event(f"Ev1{i}", q)
        client.post("/slack/events", content=body, headers=signed(body))
    drain(app, settings)
    assert len(replier.messages) == 3
    actions = [e["action"] for e in app.state.audit.read_events()]
    assert {"health", "ingest_today", "failing_pods"} <= set(actions)


def test_scale_flow_requires_operator_token_and_uses_scaler_identity(bot, settings):
    client, app, replier, scaler, _ = bot
    body = event("Ev20", "scale api to 5", user="URANDOM")
    client.post("/slack/events", content=body, headers=signed(body))
    body = event("Ev21", "scale api to 5", user=OPERATOR)
    client.post("/slack/events", content=body, headers=signed(body))
    drain(app, settings)
    assert "Không thực hiện" in replier.messages[0]["text"]
    token = replier.messages[1]["blocks"][1]["elements"][0]["value"]
    assert scaler.calls == []
    body = event("Ev22", f"confirm {token}", user=OPERATOR)
    client.post("/slack/events", content=body, headers=signed(body))
    drain(app, settings)
    assert scaler.calls == [("insighthub-api", 5)]
    last = [e for e in app.state.audit.read_events() if e["action"] == "scale"][-1]
    assert last["identity"] == "chatops-scaler" and last["decision"] == "allowed" and last["outcome"] == "ok"


def test_interactive_approve_button(bot, settings):
    client, app, replier, scaler, _ = bot
    body = event("Ev30", "scale worker to 2", user=OPERATOR)
    client.post("/slack/events", content=body, headers=signed(body))
    drain(app, settings)
    token = replier.messages[0]["blocks"][1]["elements"][0]["value"]
    payload = {"type": "block_actions", "user": {"id": OPERATOR}, "channel": {"id": "C1"},
               "container": {"message_ts": "2.2"}, "message": {"ts": "2.2", "thread_ts": "1.1"},
               "actions": [{"action_id": "chatops_approve", "value": token, "action_ts": "3.3"}]}
    form = urlencode({"payload": json.dumps(payload)}).encode()
    h = {**signed(form), "Content-Type": "application/x-www-form-urlencoded"}
    assert client.post("/slack/interactions", content=form, headers=h).status_code == 200
    drain(app, settings)
    assert scaler.calls == [("insighthub-ingestion-worker", 2)]


def test_reply_failure_retried_then_succeeds(settings):
    replier = FakeReplier(fail_times=1)
    app = create_app(settings, backend=FakeBackend(), replier=replier, scaler=FakeScaler(), start_worker=False)
    with TestClient(app) as client:
        body = event("Ev40", "health")
        client.post("/slack/events", content=body, headers=signed(body))
        drain(app, settings)
        assert app.state.queue.status("Ev40") == ("pending", 1)
        app.state.queue._conn.execute("UPDATE events SET next_at=0")
        drain(app, settings)
        assert app.state.queue.status("Ev40") == ("done", 2) and len(replier.messages) == 1


def test_healthz(bot):
    client, *_ = bot
    body = client.get("/healthz").json()
    assert body["ready"] is True and body["transport"] == "http"
