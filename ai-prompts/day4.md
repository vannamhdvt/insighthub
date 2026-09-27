# Day 4 - AI prompt log

## Môi trường

| Mục | Giá trị |
| --- | --- |
| Coding host | Claude (Cowork) cho code; Claude Code + Prometheus MCP + K8s MCP cho RCA |
| Branch | `day4-observability` (từ `day3-terraform`) |
| Cluster | minikube, namespace `insighthub-local`, kube-prometheus-stack 91.7.0 |

---

## Prompt 1 - Thiết kế observability cho đủ 5 thành phần

**Prompt:** "làm Day 4" + chọn Slack workspace riêng, RCA bằng Claude Code trên Mac.

**AI đề xuất / quyết định:**
- api đã có `/metrics`; worker chưa có HTTP server → thêm `start_http_server(9101)` và 2 metric
  `insighthub_worker_jobs_total{status}`, `insighthub_worker_job_duration_seconds`.
- redis/postgres dùng exporter chính thức (pin digest); web không có metrics → blackbox probe `/api/health`.
- Queue depth dùng `insighthub_documents_total{status="pending"}` (luôn có series) làm tín hiệu chính;
  `redis_key_size{key="arq:queue"}` chỉ phụ vì series biến mất khi queue rỗng.
- NetworkPolicy default-deny sẽ chặn cả Prometheus → thêm rule cho namespace `monitoring`.

**Verify:** `helm lint`, kubeconform; unit test worker metrics endpoint.

**Review cá nhân:**
- Đúng: 5/5 target `UP` trên `/targets` (web qua blackbox probe, api, worker, 2 exporter).
- Thiếu khi chạy thật: `kubectl port-forward svc/...` bám vào 1 pod, pod api restart khi inject chaos là port-forward chết → loadgen `chat_0`, không còn traffic, drill #1 lần đầu không fire. Sửa: chạy port-forward trong vòng lặp `while true`.
- Grafana limit 512Mi bị OOMKilled sau ~21h refresh 30s → nâng lên 1Gi.

---

## Prompt 2 - Anomaly band + promtool test

**AI đề xuất / quyết định:** band `avg_1h + max(3σ, floor)`; baseline tính với `offset 10m`.
Lần đầu viết band không offset, tự kiểm bằng tay thấy sự cố tự làm phình baseline của chính nó
(ratio 0.33 nhưng band 0.37 → không fire) → thêm `offset 10m`. Floor tránh báo động khi σ≈0 (fixture ổn định).

**Verify:** `promtool check rules` 14 rules; `promtool test rules` 3 kịch bản (baseline không fire, incident fire).

**Review cá nhân:**
- Bug AI không bắt được: series counter 5xx chỉ xuất hiện sau request 5xx đầu tiên, nên khi api khỏe thì `insighthub:http_errors:ratio_rate5m` rỗng → band rỗng → `InsightHubErrorRateAnomaly` không bao giờ fire (panel Errors "No data"). Test promtool cũ không bắt được vì giả lập series 502 luôn có giá trị 0.
  Sửa: tử số thêm `or 0 * sum by (namespace)(rate(...total[5m]))` + test mới với series 502 vắng mặt suốt baseline (fail trên rule cũ, pass sau khi sửa).
- Điểm yếu của band 1h: sau incident, band "học" luôn spike (LLM band phình lên ~7s, error band ~40%) → incident cùng loại lặp lại trong ~1h tiếp theo sẽ không bị phát hiện. Hướng cải thiện: baseline dài hơn / loại trừ khoảng đang firing, hoặc kết hợp ngưỡng tĩnh.
- Baseline cần sample thật: khi mất traffic, recording rule ghi `NaN` và làm `avg_1h` thành `NaN`; phải chờ đủ 70 phút traffic sạch rồi mới drill.

---

## Prompt 3 - RCA evidence-first (dùng trong Claude Code, 1 lần / incident)

Prompt dùng thật (copy vào Claude Code, thay N):

```
Bạn là SRE điều tra incident-N của InsightHub (namespace insighthub-local).
Quy tắc evidence-first:
1. Chỉ dùng dữ liệu đọc được qua Prometheus MCP / Kubernetes MCP và file rca-reports/incident-N.evidence.json.
2. Mọi nhận định phải cite metric + timestamp (RFC3339) + giá trị. Không có evidence thì ghi "chưa xác minh".
3. Không bịa metric: chỉ dùng tên metric xuất hiện trong evidence hoặc trả về từ query.
4. So sánh baseline (trước injected_at) với peak và recovery; nêu correlation giữa các tín hiệu.
Việc: đọc evidence, query thêm nếu cần (kubectl events/rollout history, ALERTS), rồi ghi rca-reports/incident-N.json:
{ "incident_id", "started_at", "ended_at" (copy từ evidence), "summary", "root_cause",
  "hypotheses": [3 STRING (không phải object), mỗi string = giả thuyết + evidence ủng hộ/bác bỏ + kết luận],
  "confidence" (0-1, lý do), "detection": {alert, first_firing_at, detection_seconds},
  "samples": (copy NGUYÊN mảng samples từ evidence, không sửa giá trị), "remediation", "prevention" }
```

**Kết quả / Review cá nhân:**

| Incident | Alert | detection_seconds | AI đúng ngay lần đầu? |
| --- | --- | --- | --- |
| 1 - LLM latency (DELAY=3s) | InsightHubLLMLatencyAnomaly | 262 | Không - phải sửa 2 vòng |
| 2 - Queue backlog (worker=0) | InsightHubQueueBacklogAnomaly | 190 | Có |
| 3 - Error burst (RATE=0.6) | InsightHubErrorRateAnomaly | 294 | Có |

- Tốt: AI gọi Prometheus MCP thật, cite metric + timestamp đúng, `samples` giữ nguyên (so sánh bằng script), ghi "chưa xác minh" khi thiếu dữ liệu. Incident 3 tự giải thích được vì sao ratio chỉ ~0.25 dù RATE=0.6 (chỉ `/chat` bị lỗi, các route khác pha loãng).
- Lỗi incident 1 (vòng 1): K8s MCP `connection refused` - minikube đổi cổng API sau khi Mac sleep và token ServiceAccount `mcp-readonly` hết hạn → sửa server + `kubectl create token --duration=24h`. MCP credential hết hạn làm RCA thiếu evidence mà không có cảnh báo.
- Lỗi incident 1 (vòng 2): AI kết luận "không có rollout trùng cửa sổ" và TỰ NÂNG confidence 0.85 → 0.92, dựa trên (a) ReplicaSet đang chạy có creationTimestamp từ hôm trước, (b) không thấy event lúc 15:00. Cả hai là "absence of evidence": rollback tái sử dụng RS cũ (annotation `revision-history`), Events chỉ giữ ~1h. Tôi chỉ ra → AI liệt kê đủ ReplicaSet, tìm đúng `insighthub-api-697c94c97b` có `CHAOS_LLM_DELAY_SECONDS=3`.
- Sửa prompt: `hypotheses` phải là mảng STRING (verifier yêu cầu), và thêm quy tắc 5 về Events TTL / RS reuse → incident 2, 3 đúng ngay lần đầu.
- Bài học: AI nhanh ở phần correlation metric nhưng dễ coi "không thấy" là "không có"; người review phải hỏi "dữ liệu này còn tồn tại không?" trước khi chấp nhận kết luận phủ định.
