import asyncio
import json

import httpx

from app.llm import LLMAgent
from app.skills import InfraSkills
from doubles import FakeBackend


def agent(handler, steps=3):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LLMAgent("k", "model-x", InfraSkills(FakeBackend(), "insighthub-local", "UTC"), steps, client)


def test_tool_loop_then_answer():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if len(seen) == 1:
            assert {t["name"] for t in body["tools"]} >= {"get_health", "prometheus_query"}
            assert not any("scale" in t["name"] for t in body["tools"])  # LLM không có tool mutation
            return httpx.Response(200, json={"content": [{"type": "tool_use", "id": "t1", "name": "get_failing_pods", "input": {}}]})
        result = body["messages"][-1]["content"][0]
        assert result["content"].startswith("<tool_output>")
        return httpx.Response(200, json={"content": [{"type": "text", "text": "worker đang CrashLoopBackOff"}]})

    text, used = asyncio.run(agent(handler).answer("tình hình thế nào?", "U1"))
    assert "CrashLoopBackOff" in text and used == ["get_failing_pods"]


def test_loop_is_bounded():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"content": [{"type": "tool_use", "id": f"t{len(calls)}", "name": "get_health", "input": {}}]})

    text, used = asyncio.run(agent(handler, steps=2).answer("loop", "U1"))
    assert len(calls) == 2 and "giới hạn" in text
