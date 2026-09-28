"""3 intent read-only, mỗi intent gọi 2-3 MCP tool rồi tổng hợp context.

Output của tool là dữ liệu chưa tin cậy: chỉ parse field cần, không đưa nguyên văn vào reply.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from .mcp_client import ToolBackend, ToolError


@dataclass
class Answer:
    text: str
    tools: list[str] = field(default_factory=list)
    ok: bool = True


# ---------- parsers ----------

def prom_vector(text: str) -> list[tuple[dict[str, str], float]]:
    """Nhận JSON của prometheus-mcp (data hoặc full response), trả [(labels, value)]."""
    data: Any = json.loads(text)
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if isinstance(data, dict):
        data = data.get("result", [])
    out: list[tuple[dict[str, str], float]] = []
    for row in data or []:
        try:
            value = float(row["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if math.isfinite(value):
            out.append((dict(row.get("metric", {})), value))
    return out


def yaml_list(text: str) -> list[dict[str, Any]]:
    """kubernetes-mcp-server --list-output yaml: có thể có 1 dòng mô tả trước YAML."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("- ") or line.startswith("apiVersion:") or line.startswith("items:"):
            text = "\n".join(lines[i:])
            break
    data = yaml.safe_load(text) if text.strip() else []
    if isinstance(data, dict):
        data = data.get("items", [data])
    return [x for x in (data or []) if isinstance(x, dict)]


def pod_problem(pod: dict[str, Any]) -> tuple[str, int] | None:
    """(lý do, restarts) nếu pod đang có vấn đề, None nếu khỏe."""
    status = pod.get("status", {}) or {}
    phase = status.get("phase", "Unknown")
    if phase == "Succeeded":
        return None
    statuses = status.get("containerStatuses") or []
    restarts = sum(int(c.get("restartCount", 0) or 0) for c in statuses)
    for c in statuses:
        waiting = (c.get("state") or {}).get("waiting")
        if waiting:
            return waiting.get("reason", "Waiting"), restarts
        terminated = (c.get("state") or {}).get("terminated")
        if terminated and terminated.get("exitCode", 0) != 0:
            return terminated.get("reason", "Error"), restarts
    if phase != "Running":
        return phase, restarts
    if statuses and not all(c.get("ready") for c in statuses):
        last = next(((c.get("lastState") or {}).get("terminated", {}) for c in statuses if not c.get("ready")), {})
        return "NotReady" + (f" (last: {last.get('reason')})" if last.get("reason") else ""), restarts
    return None


# ---------- skills ----------

class InfraSkills:
    def __init__(self, backend: ToolBackend, namespace: str, timezone: str) -> None:
        self.backend = backend
        self.ns = namespace
        self.tz = ZoneInfo(timezone)

    async def _prom(self, query: str, user: str, eid: str | None, used: list[str]) -> list[tuple[dict[str, str], float]]:
        used.append("prometheus.prometheus_query")
        return prom_vector(await self.backend.call("prometheus", "prometheus_query", {"query": query},
                                                   user=user, slack_event_id=eid))

    async def _k8s(self, tool: str, args: dict[str, Any], user: str, eid: str | None, used: list[str]) -> list[dict[str, Any]]:
        used.append(f"kubernetes.{tool}")
        return yaml_list(await self.backend.call("kubernetes", tool, args, user=user, slack_event_id=eid))

    async def health(self, user: str, eid: str | None = None) -> Answer:
        used: list[str] = []
        lines: list[str] = []
        problems = 0
        try:
            deps = await self._k8s("resources_list", {"apiVersion": "apps/v1", "kind": "Deployment",
                                                      "namespace": self.ns}, user, eid, used)
            for d in sorted(deps, key=lambda x: x.get("metadata", {}).get("name", "")):
                name = d.get("metadata", {}).get("name", "?")
                want = int((d.get("spec") or {}).get("replicas", 1) or 0)
                ready = int((d.get("status") or {}).get("readyReplicas", 0) or 0)
                mark = "✅" if ready >= want else "❌"
                problems += ready < want
                lines.append(f"{mark} `{name}` {ready}/{want} ready")
        except ToolError as exc:
            problems += 1
            lines.append(f"⚠️ Không đọc được Deployment: {exc}")
        try:
            targets = await self._prom(f'up{{namespace="{self.ns}"}}', user, eid, used)
            down = sorted({t.get("job", t.get("service", "?")) for t, v in targets if v < 1})
            problems += bool(down)
            lines.append(f"Scrape targets: {sum(1 for _, v in targets if v >= 1)}/{len(targets)} up"
                         + (f" — down: {', '.join(down)}" if down else ""))
            err = await self._prom(f'insighthub:http_errors:ratio_rate5m{{namespace="{self.ns}"}}', user, eid, used)
            if err:
                ratio = err[0][1]
                problems += ratio > 0.05
                lines.append(f"HTTP 5xx ratio (5m): {ratio:.1%}")
        except ToolError as exc:
            problems += 1
            lines.append(f"⚠️ Không đọc được Prometheus: {exc}")
        head = "🟢 *InsightHub healthy*" if problems == 0 else f"🔴 *InsightHub có {problems} vấn đề*"
        return Answer("\n".join([head, *lines]), used, problems == 0)

    async def ingest_today(self, user: str, eid: str | None = None) -> Answer:
        used: list[str] = []
        now = datetime.now(self.tz)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        window = max(int((now - midnight).total_seconds()), 60)
        try:
            rows = await self._prom(
                f'sum by (status) (increase(insighthub_worker_jobs_total{{namespace="{self.ns}"}}[{window}s]))',
                user, eid, used)
            backlog = await self._prom(f'insighthub:queue_backlog{{namespace="{self.ns}"}}', user, eid, used)
        except ToolError as exc:
            return Answer(f"⚠️ Không lấy được số liệu ingestion: {exc}", used, False)
        by_status = {labels.get("status", "?"): round(v) for labels, v in rows}
        total = sum(by_status.values())
        detail = ", ".join(f"{k}: {v}" for k, v in sorted(by_status.items())) or "chưa có job nào"
        pending = round(backlog[0][1]) if backlog else "n/a"
        return Answer(
            f"📄 *Hôm nay ({midnight:%d/%m} từ 00:00 {self.tz.key}) worker xử lý {total} document*\n"
            f"{detail}\nĐang chờ (pending): {pending}\n"
            "_Nguồn: increase(insighthub_worker_jobs_total); counter reset khi pod restart vẫn được increase() bù._",
            used)

    async def failing_pods(self, user: str, eid: str | None = None) -> Answer:
        used: list[str] = []
        try:
            pods = await self._k8s("pods_list_in_namespace", {"namespace": self.ns}, user, eid, used)
        except ToolError as exc:
            return Answer(f"⚠️ Không đọc được pod: {exc}", used, False)
        bad: list[tuple[str, str, int]] = []
        restarted: list[tuple[str, int]] = []
        for pod in pods:
            name = pod.get("metadata", {}).get("name", "?")
            problem = pod_problem(pod)
            if problem:
                bad.append((name, *problem))
            else:
                r = sum(int(c.get("restartCount", 0) or 0) for c in (pod.get("status") or {}).get("containerStatuses") or [])
                if r:
                    restarted.append((name, r))
        if not bad:
            text = f"🟢 Không có pod lỗi trong `{self.ns}` ({len(pods)} pod)."
            if restarted:
                text += "\nĐã từng restart: " + ", ".join(f"`{n}` ({r})" for n, r in restarted)
            return Answer(text, used)
        lines = [f"🔴 *{len(bad)}/{len(pods)} pod đang lỗi trong `{self.ns}`*"]
        lines += [f"• `{n}` — {reason}, restarts={r}" for n, reason, r in bad]
        try:
            events = await self._k8s("events_list", {"namespace": self.ns}, user, eid, used)
            names = {n for n, _, _ in bad}
            warn = [e for e in events if e.get("Type", e.get("type")) == "Warning"
                    and (e.get("InvolvedObject", e.get("involvedObject")) or {}).get("Name",
                         (e.get("InvolvedObject", e.get("involvedObject")) or {}).get("name")) in names]
            for e in warn[-3:]:
                msg = " ".join(str(e.get("Message", e.get("message", ""))).split())[:140]
                lines.append(f"  ↳ {e.get('Reason', e.get('reason', ''))}: {msg}")
        except ToolError:
            lines.append("  ↳ (không đọc được events)")
        lines.append("Gợi ý: xem log `kubectl -n %s logs <pod> --previous`; nếu cần scale: `@bot scale api to N`." % self.ns)
        return Answer("\n".join(lines), used, False)
