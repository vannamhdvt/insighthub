# Runbook — InsightHub anomaly alerts (Day 4)

Band chung: `avg_1h (offset 10m) + max(3 × stddev_1h, floor)`, `for: 2m`. Dashboard: *InsightHub - RED / USE / AIOps*.

## InsightHubLLMLatencyAnomaly
- **Ý nghĩa**: `insighthub:llm_latency_seconds:p95_5m` vượt band (floor 0.5s).
- **Kiểm tra**: panel *LLM latency p95*; so với *RAG p95* (nếu cả hai tăng → generation là nguồn);
  `kubectl -n <ns> get deploy insighthub-api -o jsonpath='{.spec.template.spec.containers[0].env}'` (CHAOS_LLM_DELAY_SECONDS?);
  CPU api (panel *Pod CPU*) chạm limit?; real mode: status page provider.
- **Xử lý**: gỡ fault/rollback deploy gần nhất (panel *Deployments*); tăng timeout phía client không phải là fix.

## InsightHubQueueBacklogAnomaly
- **Ý nghĩa**: `insighthub:queue_backlog` (documents `pending`) vượt band (floor 5).
- **Kiểm tra**: `kubectl -n <ns> get deploy insighthub-ingestion-worker` (replicas 0?); panel *Worker throughput*
  (= 0 → worker không chạy); `redis_key_size{key="arq:queue"}`; log `ingestion_started` / `ingestion_completed`.
- **Xử lý**: khôi phục worker (`kubectl scale ... --replicas=1`), sửa REDIS_URL lệch; backlog tự drain.

## InsightHubErrorRateAnomaly
- **Ý nghĩa**: `insighthub:http_errors:ratio_rate5m` vượt band (floor 5%) — **critical**.
- **Kiểm tra**: panel *Rate by endpoint* + `sum by (endpoint,status) (rate(insighthub_http_requests_total{status=~"5.."}[5m]))`;
  502 `provider_error` → provider/fault injection (CHAOS_LLM_ERROR_RATE); 503 → DB/schema; `kubectl logs deploy/insighthub-api`.
- **Xử lý**: gỡ fault/rollback; nếu provider lỗi thật: chuyển provider dự phòng (Day 6 gateway), không tự rơi về fixture.
