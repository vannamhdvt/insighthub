"""Xử lý 1 job từ queue: event -> filter -> route -> permission -> tool/approval -> audit -> reply."""
from __future__ import annotations

from typing import Any

from .audit import AuditLog
from .executor import ExecutionError, Scaler
from .intents import Intent, route
from .llm import LLMAgent
from .permissions import ApprovalError, ApprovalStore, PermissionPolicy
from .skills import InfraSkills
from .slack import Replier, approval_blocks

HELP = (
    "Mình trả lời được:\n"
    "• `InsightHub có healthy không?`\n"
    "• `Hôm nay ingest bao nhiêu doc?`\n"
    "• `Pod nào đang lỗi?`\n"
    "• `scale api to 3` (operator, cần confirm token 60s)"
)


class ChatOpsService:
    def __init__(self, *, skills: InfraSkills, policy: PermissionPolicy, approvals: ApprovalStore,
                 scaler: Scaler, replier: Replier, audit: AuditLog, bot_user_id: str = "",
                 llm: LLMAgent | None = None) -> None:
        self.skills = skills
        self.policy = policy
        self.approvals = approvals
        self.scaler = scaler
        self.replier = replier
        self.audit = audit
        self.bot_user_id = bot_user_id
        self.llm = llm

    # ---------- entry ----------
    async def handle(self, payload: dict[str, Any]) -> None:
        if payload.get("kind") == "interaction":
            await self._handle_interaction(payload["body"])
            return
        body = payload["body"]
        event = body.get("event") or {}
        if self._ignore(event):
            return
        eid = body.get("event_id")
        user, channel = event.get("user", ""), event.get("channel", "")
        thread = event.get("thread_ts") or event.get("ts")
        intent = route(event.get("text", ""))
        text, blocks = await self.dispatch(intent, user, channel, eid)
        await self.replier.post(channel, text, thread_ts=thread, blocks=blocks)

    def _ignore(self, event: dict[str, Any]) -> bool:
        # Chống vòng lặp: bỏ message của chính bot / bot khác / message edit-delete.
        if event.get("bot_id") or event.get("subtype"):
            return True
        if self.bot_user_id and event.get("user") == self.bot_user_id:
            return True
        return event.get("type") not in {"app_mention", "message"}

    # ---------- routing ----------
    async def dispatch(self, intent: Intent, user: str, channel: str,
                       eid: str | None) -> tuple[str, list[dict[str, Any]] | None]:
        decision = self.policy.decide(user, intent)
        if decision.decision == "denied":
            self.audit.record(user=user, action=decision.action, tier=decision.tier, args=decision.args,
                              decision="denied", result=decision.reason, outcome="error", slack_event_id=eid)
            return f"⛔ Không thực hiện `{decision.action}`: {decision.reason}.", None
        if intent.name == "scale":
            item = self.approvals.issue("scale", decision.args, user, channel)
            self.audit.record(user=user, action="scale", tier="write", args=decision.args,
                              decision="approval_required", result=f"token issued, ttl={self.approvals.ttl}s",
                              outcome="pending", slack_event_id=eid)
            text = (f"⚠️ Yêu cầu *scale* `{decision.args['deployment']}` → *{decision.args['replicas']}* replicas.\n"
                    f"Operator xác nhận trong {self.approvals.ttl}s: `@bot confirm {item.token}` "
                    f"(hoặc `@bot cancel {item.token}`).")
            return text, approval_blocks(text, item.token)
        if intent.name == "confirm":
            return await self.confirm(intent.params["token"], user, channel, eid), None
        if intent.name == "cancel":
            ok = self.approvals.cancel(intent.params["token"])
            self.audit.record(user=user, action="cancel", tier="write", args={"token_prefix": intent.params["token"][:3]},
                              decision="allowed", result="cancelled" if ok else "nothing to cancel",
                              slack_event_id=eid)
            return ("🛑 Đã huỷ yêu cầu." if ok else "Không có yêu cầu nào đang chờ với token đó."), None
        self.audit.record(user=user, action=intent.name, tier="read", decision="allowed",
                          result="read intent", slack_event_id=eid)
        if intent.name == "health":
            ans = await self.skills.health(user, eid)
        elif intent.name == "ingest_today":
            ans = await self.skills.ingest_today(user, eid)
        elif intent.name == "failing_pods":
            ans = await self.skills.failing_pods(user, eid)
        elif intent.name == "unknown" and self.llm is not None:
            text, used = await self.llm.answer(intent.params.get("text", ""), user, eid)
            return f"{text}\n_tools: {', '.join(used) or 'none'} (LLM)_", None
        else:
            return HELP, None
        return f"{ans.text}\n_tools: {', '.join(ans.tools)}_", None

    async def confirm(self, token: str, user: str, channel: str, eid: str | None) -> str:
        pending = self.approvals.peek(token)
        try:
            if pending is None:
                raise ApprovalError("unknown token")
            item = self.approvals.redeem(token, user, channel, pending.action, pending.args)
        except ApprovalError as exc:
            self.audit.record(user=user, action="confirm", tier="write", args={"token_prefix": token[:3]},
                              decision="denied", result=str(exc),
                              outcome="expired" if "expired" in str(exc) else "error", slack_event_id=eid)
            return f"⛔ Không xác nhận được: {exc}."
        return await self.execute(item.action, item.args, requester=item.requester, approver=user, eid=eid)

    async def execute(self, action: str, args: dict[str, Any], *, requester: str, approver: str,
                      eid: str | None) -> str:
        identity = getattr(self.scaler, "identity", "chatops-scaler")
        try:
            out = await self.scaler.scale(args["deployment"], args["replicas"])
        except ExecutionError as exc:
            self.audit.record(user=approver, action=action, tier="write", tool="kubectl.scale", args=args,
                              decision="allowed", identity=identity, outcome="error",
                              result=f"requested_by={requester}; {exc}", slack_event_id=eid)
            return f"❌ Scale thất bại: {exc}"
        self.audit.record(user=approver, action=action, tier="write", tool="kubectl.scale", args=args,
                          decision="allowed", identity=identity, outcome="ok",
                          result=f"requested_by={requester}; {out}", slack_event_id=eid)
        return f"✅ Đã scale `{args['deployment']}` → {args['replicas']} (duyệt bởi <@{approver}>, identity `{identity}`)."

    async def _handle_interaction(self, body: dict[str, Any]) -> None:
        user = (body.get("user") or {}).get("id", "")
        channel = (body.get("channel") or {}).get("id", "")
        msg = body.get("message") or {}
        thread = msg.get("thread_ts") or msg.get("ts")
        for action in body.get("actions") or []:
            token = str(action.get("value", ""))
            name = "confirm" if action.get("action_id") == "chatops_approve" else "cancel"
            text, _ = await self.dispatch(Intent(name, {"token": token}), user, channel, body.get("trigger_id"))
            await self.replier.post(channel, text, thread_ts=thread)
