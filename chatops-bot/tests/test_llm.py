import asyncio
import json

import httpx
import pytest

from app.llm import LLMAgent, LLMError
from app.skills import InfraSkills
from doubles import FakeBackend


def agent(handler, steps=3):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LLMAgent("http://gw/v1", "sk-bot", "chatops-agent", InfraSkills(FakeBackend(), "insighthub-local", "UTC"),
                    steps, client)


def tool_call(i, name):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": "{}"}}


def test_tool_loop_via_gateway_then_answer():
    seen = []

    def handler(request):
        assert request.url == "http://gw/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-bot"
        body = json.loads(request.content)
        seen.append(body)
        assert body["model"] == "chatops-agent" and "workload:chatops-bot" in body["metadata"]["tags"]
        if len(seen) == 1:
            names = {t["function"]["name"] for t in body["tools"]}
            assert {"get_health", "prometheus_query"} <= names
            assert not any("scale" in n for n in names)  # no mutation tool for the LLM
            return httpx.Response(200, json={"choices": [{"message": {"content": None,
                                                                      "tool_calls": [tool_call(1, "get_failing_pods")]}}]})
        tool_msg = body["messages"][-1]
        assert tool_msg["role"] == "tool" and tool_msg["content"].startswith("<tool_output>")
        return httpx.Response(200, json={"choices": [{"message": {"content": "worker đang CrashLoopBackOff"}}]})

    text, used = asyncio.run(agent(handler).answer("tình hình thế nào?", "U1"))
    assert "CrashLoopBackOff" in text and used == ["get_failing_pods"]


def test_loop_is_bounded():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [tool_call(len(calls), "get_health")]}}]})

    text, used = asyncio.run(agent(handler, steps=2).answer("loop", "U1"))
    assert len(calls) == 2 and "giới hạn" in text


@pytest.mark.parametrize("body,msg", [
    ({"error": {"message": "insighthub_guardrail_blocked"}}, "guardrail"),
    ({"error": {"message": "Budget has been exceeded!", "type": "budget_exceeded"}}, "ngân sách"),
])
def test_gateway_refusals_are_explained(body, msg):
    handler = lambda request: httpx.Response(429 if "Budget" in json.dumps(body) else 400, json=body)  # noqa: E731
    with pytest.raises(LLMError, match=msg):
        asyncio.run(agent(handler).answer("x", "U1"))
