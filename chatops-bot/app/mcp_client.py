"""MCP client: tái sử dụng Kubernetes MCP + Prometheus MCP của Day 2 qua stdio.

Config ở `mcp-servers.json` (cùng version pin với .mcp.json Day 2). Bot chỉ gọi tool
nằm trong `allow_tools` của từng server; server K8s chạy `--read-only --disable-destructive`
với kubeconfig của ServiceAccount chatops-readonly. Hai lớp chặn độc lập: client allowlist + RBAC.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Protocol

from .audit import AuditLog

_VAR = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")
_MAX_TEXT = 200_000
_SECRET_ENV = re.compile(r"(SLACK|ANTHROPIC|OPENAI|GEMINI|LITELLM|TOKEN|SECRET|PASSWORD|API_KEY|KUBECONFIG$)", re.I)


class ToolError(Exception):
    pass


class ToolBackend(Protocol):
    async def call(self, server: str, tool: str, args: dict[str, Any], *, user: str,
                   slack_event_id: str | None = None) -> str: ...


def expand(value: str) -> str:
    def sub(m: re.Match[str]) -> str:
        return os.environ.get(m.group(1)) or (m.group(2) or "")
    return _VAR.sub(sub, value)


def load_server_config(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    servers: dict[str, dict[str, Any]] = {}
    for name, spec in data["servers"].items():
        servers[name] = {
            "command": expand(spec["command"]),
            "args": [expand(a) for a in spec.get("args", [])],
            "env": {k: expand(v) for k, v in spec.get("env", {}).items()},
            "allow_tools": frozenset(spec["allow_tools"]),
        }
    return servers


def summarize(text: str, limit: int = 160) -> str:
    one = " ".join(text.split())
    return one if len(one) <= limit else one[:limit] + "..."


class MCPBackend:
    """Giữ 1 session stdio lâu dài / server; lỗi thì đóng và mở lại ở lần gọi sau."""

    def __init__(self, config_path: Path, audit: AuditLog, timeout: float = 15.0) -> None:
        self.servers = load_server_config(config_path)
        self.audit = audit
        self.timeout = timeout
        self._sessions: dict[str, Any] = {}
        self._stacks: dict[str, AsyncExitStack] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _session(self, server: str) -> Any:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        if server in self._sessions:
            return self._sessions[server]
        spec = self.servers[server]
        # Giữ env cần cho node/npx (PATH, HOME, proxy, CA...) nhưng không truyền secret sang process MCP.
        env = {k: v for k, v in os.environ.items() if not _SECRET_ENV.search(k)}
        env.update(spec["env"])
        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(command=spec["command"], args=spec["args"], env=env)
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(session.initialize(), timeout=max(self.timeout, 60))
        except BaseException:
            await stack.aclose()
            raise
        self._stacks[server], self._sessions[server] = stack, session
        return session

    async def _reset(self, server: str) -> None:
        self._sessions.pop(server, None)
        stack = self._stacks.pop(server, None)
        if stack is not None:
            try:
                await stack.aclose()
            except BaseException:  # noqa: BLE001 - process đã chết thì bỏ qua
                pass

    async def call(self, server: str, tool: str, args: dict[str, Any], *, user: str,
                   slack_event_id: str | None = None) -> str:
        spec = self.servers.get(server)
        action = f"mcp:{server}.{tool}"
        if spec is None or tool not in spec["allow_tools"]:
            self.audit.record(user=user, action=action, tool=tool, args=args, decision="denied", tier="read",
                              result="tool not in allowlist", outcome="error", slack_event_id=slack_event_id)
            raise ToolError(f"tool {server}.{tool} is not allowed")
        lock = self._locks.setdefault(server, asyncio.Lock())
        started = time.monotonic()
        try:
            async with lock:
                session = await self._session(server)
                result = await asyncio.wait_for(session.call_tool(tool, args), timeout=self.timeout)
            text = "\n".join(getattr(c, "text", "") for c in result.content)[:_MAX_TEXT]
            if result.isError:
                raise ToolError(summarize(text) or "tool returned error")
        except ToolError as exc:
            self._audit(user, action, tool, args, str(exc), "error", slack_event_id, started)
            raise
        except Exception as exc:  # noqa: BLE001 - timeout/process chết: reset session, báo lỗi gọn
            await self._reset(server)
            self._audit(user, action, tool, args, type(exc).__name__, "error", slack_event_id, started)
            raise ToolError(f"{server}.{tool} unavailable ({type(exc).__name__})") from exc
        self._audit(user, action, tool, args, summarize(text), "ok", slack_event_id, started)
        return text

    def _audit(self, user: str, action: str, tool: str, args: dict[str, Any], result: str, outcome: str,
               slack_event_id: str | None, started: float) -> None:
        self.audit.record(user=user, action=action, tool=tool, args=args, decision="allowed", tier="read",
                          result=result, outcome=outcome, slack_event_id=slack_event_id,
                          duration_ms=int((time.monotonic() - started) * 1000))

    async def aclose(self) -> None:
        for server in list(self._sessions):
            await self._reset(server)
