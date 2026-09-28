"""InsightHub ChatOps bot — HTTP transport (Slack Events API).

Thứ tự trong request, cố định:
  1. đọc RAW body   2. verify signature + timestamp (<=5 phút)   3. mới parse JSON / trả challenge
  4. enqueue bền vững + dedup event_id   5. ACK 200 ngay (<3s).
Câu trả lời (MCP/LLM) do worker nền gửi sau qua chat.postMessage, có deadline riêng.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request

from .audit import AuditLog
from .config import Settings, load_settings
from .executor import KubectlScaler, Scaler
from .llm import LLMAgent
from .mcp_client import MCPBackend, ToolBackend
from .permissions import ApprovalStore, PermissionPolicy, load_scale_targets
from .queue import EventQueue
from .service import ChatOpsService
from .signature import SignatureError, verify_slack_signature
from .skills import InfraSkills
from .slack import Replier, SlackReplier
from .worker import run_worker

logger = logging.getLogger("chatops-bot")


def create_app(settings: Settings | None = None, *, backend: ToolBackend | None = None,
               replier: Replier | None = None, scaler: Scaler | None = None,
               start_worker: bool = True) -> FastAPI:
    settings = settings or load_settings()
    audit = AuditLog(settings.audit_log)
    queue = EventQueue(settings.queue_db)
    owned_backend = backend is None
    backend = backend or MCPBackend(settings.mcp_config, audit, timeout=settings.tool_timeout)
    skills = InfraSkills(backend, settings.namespace, settings.timezone)
    llm = (LLMAgent(settings.litellm_base_url, settings.litellm_api_key, settings.llm_model, skills,
                    settings.llm_max_steps) if settings.llm_enabled else None)
    service = ChatOpsService(
        skills=skills,
        policy=PermissionPolicy(settings.operator_ids, load_scale_targets(settings.catalog)),
        approvals=ApprovalStore(settings.approval_ttl),
        scaler=scaler or KubectlScaler(settings.kubectl, settings.scaler_kubeconfig, settings.namespace),
        replier=replier or SlackReplier(settings.bot_token),
        audit=audit,
        bot_user_id=settings.bot_user_id,
        llm=llm,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = asyncio.Event()
        task = None
        if start_worker:
            task = asyncio.create_task(run_worker(queue, service, settings.answer_deadline,
                                                  settings.worker_max_attempts, stop))
        yield
        stop.set()
        if task:
            await task
        if owned_backend and isinstance(backend, MCPBackend):
            await backend.aclose()

    app = FastAPI(title="InsightHub ChatOps bot", version="1.0.0", lifespan=lifespan)
    app.state.settings, app.state.queue, app.state.service, app.state.audit = settings, queue, service, audit

    async def verified_body(request: Request) -> bytes:
        raw = await request.body()
        try:
            verify_slack_signature(settings.signing_secret, request.headers.get("X-Slack-Request-Timestamp"),
                                   request.headers.get("X-Slack-Signature"), raw, settings.signature_max_age)
        except SignatureError as exc:
            # Không log body/headers của request chưa xác thực.
            logger.warning("rejected slack request: %s", exc)
            raise HTTPException(status_code=401, detail="invalid signature") from None
        return raw

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        ready = bool(settings.signing_secret and settings.bot_token)
        return {"status": "ok" if ready else "degraded", "ready": ready, "transport": "http",
                "queue_depth": queue.depth(), "llm": settings.llm_enabled,
                "scaler_configured": bool(settings.scaler_kubeconfig)}

    @app.post("/slack/events")
    async def slack_events(request: Request) -> dict[str, Any]:
        raw = await verified_body(request)
        try:
            body = json.loads(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid json") from None
        if body.get("type") == "url_verification":
            return {"challenge": body.get("challenge", "")}
        if body.get("type") != "event_callback" or not body.get("event_id"):
            return {"ok": True, "ignored": True}
        is_new = queue.enqueue(body["event_id"], {"kind": "event", "body": body})
        return {"ok": True, "duplicate": not is_new}

    @app.post("/slack/interactions")
    async def slack_interactions(request: Request) -> dict[str, Any]:
        raw = await verified_body(request)
        try:
            body = json.loads(parse_qs(raw.decode())["payload"][0])
        except (KeyError, IndexError, ValueError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="invalid payload") from None
        actions = body.get("actions") or []
        if body.get("type") != "block_actions" or not actions:
            return {"ok": True, "ignored": True}
        key = f"int:{(body.get('container') or {}).get('message_ts', '')}:{actions[0].get('action_ts', '')}"
        is_new = queue.enqueue(key, {"kind": "interaction", "body": body})
        return {"ok": True, "duplicate": not is_new}

    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    """`uvicorn app.main:app`: tạo app lần đầu được truy cập (đọc env lúc đó).
    Import module trong test không tạo queue/log mặc định."""
    global _app
    if name != "app":
        raise AttributeError(name)
    if _app is None:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        _app = create_app()
    return _app
