# Runbook Day 4 — Observability, anomaly, 3 incident + AI RCA (local minikube)

Toàn bộ chạy local (minikube, namespace `insighthub-local`), không cần AWS.
Thứ tự: stack → deploy app có monitoring → baseline ≥ 1h → 3 drill → RCA → evidence.

## 1. Monitoring stack
```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts && helm repo update
kubectl create namespace monitoring
# Slack: tạo workspace riêng + channel #alerts → api.slack.com/apps → Create App → Incoming Webhooks → Add to #alerts
kubectl -n monitoring create secret generic alertmanager-slack --from-literal=webhook='https://hooks.slack.com/services/XXX/YYY/ZZZ'
helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack --version 91.7.0 \
  -n monitoring -f observability/kube-prometheus-stack-values.yaml --wait --timeout 10m
helm upgrade --install prometheus-blackbox-exporter prometheus-community/prometheus-blackbox-exporter --version 11.19.1 \
  -n monitoring -f observability/blackbox-exporter-values.yaml --wait
```

## 2. App có ServiceMonitor/exporters + rules + dashboard
```bash
docker build --load -t insighthub-api:local api
docker build --load -t insighthub-worker:local -f ingestion-worker/Dockerfile .
for i in api worker; do minikube image load insighthub-$i:local; done
helm upgrade --install insighthub infra/helm/insighthub -n insighthub-local -f infra/helm/insighthub/values-local.yaml --wait --timeout 10m
observability/apply-rules.sh
observability/apply-dashboard.sh
kubectl -n insighthub-local get servicemonitor,probe
```

## 3. Port-forward (1 tab riêng, để chạy suốt)
```bash
kubectl -n insighthub-local port-forward svc/insighthub-api 18000:8000 &
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090 &
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3001:80 &
kubectl -n monitoring port-forward svc/kube-prometheus-stack-alertmanager 9093:9093 &
```
Grafana http://localhost:3001 (admin / insighthub-local) → dashboard *InsightHub - RED / USE / AIOps*.
Targets: `curl -s localhost:9090/api/v1/targets | python3 -c "import json,sys;[print(t['labels'].get('job'),t['health']) for t in json.load(sys.stdin)['data']['activeTargets'] if 'insighthub' in t['labels'].get('job','')]"`

## 4. Kiểm tra Slack trước khi drill
```bash
curl -s -XPOST localhost:9093/api/v2/alerts -H 'content-type: application/json' -d '[{"labels":{"alertname":"InsightHubTestAlert","severity":"warning","namespace":"insighthub-local","component":"test"},"annotations":{"summary":"Test alert Day 4","description":"Alertmanager -> Slack path check","runbook_url":"n/a"}}]'
```
Tin nhắn phải xuất hiện ở #alerts trong ~30s (chụp màn hình).

## 5. Baseline ≥ 1h (tab riêng)
```bash
python3 observability/loadgen.py --api-url http://localhost:18000
```
Không drill khi baseline < 1h (band chưa có nghĩa).

## 6. Ba drill (mỗi cái ~15 phút; cách nhau ≥ 15 phút để band ổn định lại)
```bash
scripts/chaos/inject-llm-latency.sh      # incident-1
scripts/chaos/inject-queue-backlog.sh    # incident-2
scripts/chaos/inject-error-burst.sh      # incident-3
```
Mỗi drill ghi `evidence/day4/incident-N.window.json`. Theo dõi alert: http://localhost:9090/alerts và Slack.

## 7. AI RCA (Claude Code + Prometheus MCP + K8s MCP)
```bash
python3 observability/rca_evidence.py --window evidence/day4/incident-1.window.json --out rca-reports/incident-1.evidence.json
```
Trong Claude Code dùng prompt ở `ai-prompts/day4.md` (Prompt RCA evidence-first) → ghi `rca-reports/incident-1.json`.
Lặp lại cho incident-2, incident-3. **Không sửa tay `samples`**: verifier so từng giá trị với Prometheus.

## 8. Evidence + verify (trong vòng 24h sau drill)
```bash
python3 observability/build_day4_evidence.py
.venv-verify/bin/python scripts/verify.py day4 --evidence-dir evidence --prometheus-url http://localhost:9090
```
Cần `promtool` (brew install prometheus). Chụp: dashboard (9+ panels có data), /targets, alert firing, Slack.

## 9. Dọn
Tắt loadgen/port-forward. Giữ Prometheus data nếu còn cần verify; không có tài nguyên cloud.
