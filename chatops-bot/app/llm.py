"""Optional multi-step tool loop (Anthropic Messages API) cho câu hỏi ngoài 3 intent.

- Chỉ bật khi có ANTHROPIC_API_KEY + CHATOPS_LLM_MODEL. Không có key -> bot vẫn chạy bằng router.
- Bounded: tối đa `max_steps` vòng tool, tổng deadline do worker áp.
- Chỉ expose tool READ (3 skill + PromQL instant query). Không có tool mutation: LLM không thể scale.
- Tool output bọc trong <tool_output> và system prompt coi đó là dữ liệu, không phải lệnh.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .config import BOT_ROOT
from .skills import InfraSkills

API_URL = "https://api.anthropic.com/v1/messages"

TOOLS: list[dict[str, Any]] = [
    {"name": "get_health", "description": "Deployment readiness, scrape targets and 5xx ratio of InsightHub.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_ingest_today", "description": "Documents processed by the ingestion worker since local midnight.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_failing_pods", "description": "Pods in the InsightHub namespace that are failing, with warning events.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "prometheus_query", "description": "Instant PromQL query (read-only) against the InsightHub Prometheus.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "maxLength": 300}},
                      "required": ["query"]}},
]


def system_prompt() -> str:
    return (BOT_ROOT / "prompts" / "system.md").read_text(encoding="utf-8")


class LLMAgent:
    def __init__(self, api_key: str, model: str, skills: InfraSkills, max_steps: int = 4,
                 client: httpx.AsyncClient | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.skills = skills
        self.max_steps = max_steps
        self.client = client or httpx.AsyncClient(timeout=30)

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

    async def answer(self, question: str, user: str, eid: str | None = None) -> tuple[str, list[str]]:
        messages: list[dict[str, Any]] = [{"role": "user", "content": question[:1000]}]
        used: list[str] = []
        for _ in range(self.max_steps):
            resp = await self.client.post(API_URL, headers={
                "x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json",
            }, json={"model": self.model, "max_tokens": 700, "system": system_prompt(),
                     "tools": TOOLS, "messages": messages})
            resp.raise_for_status()
            body = resp.json()
            content = body.get("content", [])
            messages.append({"role": "assistant", "content": content})
            calls = [c for c in content if c.get("type") == "tool_use"]
            if not calls:
                return "".join(c.get("text", "") for c in content if c.get("type") == "text").strip(), used
            results = []
            for call in calls:
                used.append(call["name"])
                try:
                    out = await self._run_tool(call["name"], call.get("input") or {}, user, eid)
                except Exception as exc:  # noqa: BLE001 - lỗi tool trả về cho model như dữ liệu
                    out = f"error: {type(exc).__name__}"
                results.append({"type": "tool_result", "tool_use_id": call["id"],
                                "content": f"<tool_output>\n{out[:4000]}\n</tool_output>"})
            messages.append({"role": "user", "content": results})
        return "Mình dừng sau %d bước tool (giới hạn an toàn). Hãy hỏi cụ thể hơn." % self.max_steps, used
