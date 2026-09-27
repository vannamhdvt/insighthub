"""Day 4 static contract: rules tested by promtool, dashboard coverage, 5 scrape targets."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("INSIGHTHUB_REPO_ROOT", ".")).resolve()
OBS = ROOT / "observability"
CHART = ROOT / "infra" / "helm" / "insighthub"


def run(argv, cwd=ROOT):
    tool = shutil.which(argv[0])
    assert tool, f"required tool not installed: {argv[0]}"
    return subprocess.run([tool, *argv[1:]], capture_output=True, text=True, cwd=cwd, timeout=120)


def test_rules_pass_promtool_check_and_unit_tests():
    rules = OBS / "rules"
    check = run(["promtool", "check", "rules", "insighthub-rules.yaml"], cwd=rules)
    assert check.returncode == 0, check.stdout + check.stderr
    tests = run(["promtool", "test", "rules", "insighthub-rules.test.yaml"], cwd=rules)
    assert tests.returncode == 0, tests.stdout + tests.stderr


def test_three_anomaly_alerts_with_runbooks():
    groups = yaml.safe_load((OBS / "rules/insighthub-rules.yaml").read_text())["groups"]
    alerts = {r["alert"]: r for g in groups for r in g["rules"] if "alert" in r}
    assert set(alerts) == {"InsightHubLLMLatencyAnomaly", "InsightHubQueueBacklogAnomaly", "InsightHubErrorRateAnomaly"}
    for rule in alerts.values():
        assert rule["for"] in {"2m", "3m", "5m"}, "for must exceed scrape interval (no flapping)"
        assert rule["annotations"]["runbook_url"].endswith("#" + rule["alert"].lower())


def test_dashboard_has_nine_plus_query_panels_covering_spec():
    dash = json.loads((OBS / "grafana-dashboards/insighthub.json").read_text())
    panels = [p for p in dash["panels"] if any(t.get("expr") for t in p.get("targets", []))]
    assert len(panels) >= 9
    text = json.dumps(dash)
    for signal in ("insighthub_http_requests_total", "insighthub:http_errors:ratio_rate5m",
                   "insighthub:rag_latency_seconds:p95_5m", "insighthub:queue_backlog",
                   "insighthub_llm_tokens_total", "insighthub:llm_latency_seconds:p95_5m",
                   "price_embedding_per_mtok", "container_cpu_usage_seconds_total",
                   "kube_deployment_status_observed_generation"):
        assert signal in text, signal


def test_chart_scrapes_all_five_components():
    out = run(["helm", "template", "insighthub", str(CHART), "-n", "insighthub-local",
               "-f", str(CHART / "values-local.yaml")])
    assert out.returncode == 0, out.stderr
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    monitors = {d["metadata"]["name"] for d in docs if d["kind"] == "ServiceMonitor"}
    assert monitors == {"insighthub-api", "insighthub-ingestion-worker", "insighthub-redis-exporter", "insighthub-postgres-exporter"}
    probes = [d for d in docs if d["kind"] == "Probe"]
    assert probes and probes[0]["spec"]["targets"]["staticConfig"]["static"][0].endswith("/api/health")
    for d in docs:
        if d["kind"] in {"ServiceMonitor", "Probe"}:
            assert d["metadata"]["labels"]["release"] == "kube-prometheus-stack"
