# Observability và MLOps — Day 4

| Path | Nội dung |
|---|---|
| `kube-prometheus-stack-values.yaml` | Prometheus (retention 15d, limits), Alertmanager → Slack `#alerts` (webhook trong Secret), Grafana sidecar |
| `blackbox-exporter-values.yaml` | probe web `/api/health` (Next.js không có /metrics) |
| `rules/insighthub-rules.yaml` + `.test.yaml` | recording rules (RED, p95, backlog) + anomaly band + 3 alert; `promtool test rules` |
| `apply-rules.sh` | bọc đúng file rules thành PrometheusRule (1 nguồn sự thật) |
| `grafana-dashboards/insighthub.json` + `apply-dashboard.sh` | 13 panels: rate / errors / duration / LLM p95 / queue / worker / token / cost / CPU / memory / deploy / targets / documents |
| `loadgen.py` | traffic nền cho baseline ≥ 1h |
| `rca_evidence.py` | lấy sample thật từ Prometheus cho RCA (evidence-first) |
| `build_day4_evidence.py` | ghi `evidence/day4.json` cho `scripts/verify.py day4` |
| `mlops-overview-notes.md` | 4 block MLOps |

5 thành phần: api `/metrics`, ingestion-worker `:9101/metrics`, redis_exporter, postgres_exporter,
blackbox probe web — ServiceMonitor/Probe trong chart `infra/helm/insighthub` (`monitoring.enabled`).
Fault injection: `CHAOS_LLM_DELAY_SECONDS`, `CHAOS_LLM_ERROR_RATE` (mặc định tắt) + `scripts/chaos/`.
Runbook: [docs/runbooks/day4-observability.md](../docs/runbooks/day4-observability.md), alert: [day4-alerts.md](../docs/runbooks/day4-alerts.md).
