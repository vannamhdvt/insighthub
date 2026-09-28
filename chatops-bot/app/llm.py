"""Optional multi-step tool loop for questions outside the 3 intents, via the LiteLLM gateway.

- Day 6: the bot never talks to a provider directly. It calls the OpenAI-compatible
  gateway with its own virtual key (alias "chatops-bot", model "chatops-agent"), so it is
  covered by the gateway guardrail, per-key budget and cost attribution.
- Enabled only when LITELLM_BASE_URL + CHATOPS_LITELLM_KEY are set; otherwise the bot runs
  on the rule router alone.
- Bounded: at most `max_steps` tool rounds; the worker applies an overall deadline.
- Only READ tools are exposed (3 skills + fixed-shape PromQL). No mutation tool: the LLM
  cannot scale anything; scale stays behind the regex + approval token path.
- Tool output is wrapped in <tool_output> and the system prompt treats it as data.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from .config import BOT_ROOT
from .skills import InfraSkills


def _tool(name: str, description: str, params: dict | None = None, required: list[str] | None = None) -> dict:
    schema: dict[str, Any] = {"type": "object", "properties": params or {}}
    if required:
        schema["required"] = required
    return {"type": "function", "function": {"name": name, "description": description, "parameters": schema}}


TOOLS: list[dict[str, Any]] = [
    _tool("get_health", "Deployment readiness, scrape targets and 5xx ratio of InsightHub."),
    _tool("get_ingest_today", "Documents processed by the ingestion worker since local midnight."),
    _tool("get_failing_pods", "Pods in the InsightHub namespace that are failing, with warning events."),
    _tool("prometheus_query", "Instant PromQL query (read-only) against the InsightHub Prometheus.",
          {"query": {"type": "string", "maxLength": 300}}, ["query"]),
]


def system_prompt() -> str:
    return (BOT_ROOT / "prompts" / "system.md").read_text(encoding="utf-8")


class LLMError(Exception):
    """Gateway refused (guardrail/budget) or failed; message is safe to show in Slack."""


class LLMAgent:
    def __init__(self, base_url: str, api_key: str, model: str, skills: InfraSkills, max_steps: int = 4,
                 client: httpx.AsyncClient | None = None) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.skills = skills
        self.max_steps = max_steps
        self.client = client or httpx.AsyncClient(timeout=30, trust_env=False)

    async def _run_tool(self, name: str, args: dict[str, Any], user: str, eid: str | None) -> str:
        if name == "get_health":
            return (await self.skills.health(user, eid)).text
        if name == "get_ingest_today":
            return (await self.skills.ingest_today(user, eid)).text
        if name == "get_failing_pods":
            return (await self.skills.failing_pods(user, eid)).text
        if name == "prometheus_query":
            query = str(args.get("query", ""))[:300]
            return await self.skills.backend.call("prometheus", "prometheus_query", {"query": query},
                                                  user=user, slack_event_id=eid)
        return f"unknown tool {name}"

    async def _complete(self, messages: list[dict[str, Any]], user: str) -> dict[str, Any]:
        resp = await self.client.post(self.url, headers={"Authorization": f"Bearer {self.api_key}"}, json={
            "model": self.model, "max_tokens": 700, "messages": messages, "tools": TOOLS,
            "user": user, "metadata": {"tags": ["workload:chatops-bot", "route:slack"]},
        })
        if resp.status_code >= 400:
            marker = resp.text[:2000].lower()
            if "insighthub_guardrail" in marker:
                raise LLMError("Câu hỏi bị guardrail chặn (vi phạm chính sách an toàn).")
            if "budget_exceeded" in marker or "budget has been exceeded" in marker:
                raise LLMError("Bot đã hết ngân sách LLM của tháng; chỉ còn 3 intent cố định.")
            raise LLMError(f"LLM gateway lỗi HTTP {resp.status_code}.")
        return resp.json()

    async def answer(self, question: str, user: str, eid: str | None = None) -> tuple[str, list[str]]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": question[:1000]},
        ]
        used: list[str] = []
        for _ in range(self.max_steps):
            body = await self._complete(messages, user)
            msg = body["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls} if calls
                            else {"role": "assistant", "content": msg.get("content") or ""})
            if not calls:
                return (msg.get("content") or "").strip(), used
            for call in calls:
                name = call["function"]["name"]
                used.append(name)
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                    out = await self._run_tool(name, args, user, eid)
                except Exception as exc:  # noqa: BLE001 - tool errors go back to the model as data
                    out = f"error: {type(exc).__name__}"
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": f"<tool_output>\n{out[:4000]}\n</tool_output>"})
        return "Mình dừng sau %d bước tool (giới hạn an toàn). Hãy hỏi cụ thể hơn." % self.max_steps, used
