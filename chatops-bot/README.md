# InsightHub ChatOps bot — Day 5

Slack bot on-call: hỏi 1 câu trong Slack → bot đọc K8s + Prometheus qua **MCP Day 2** → trả lời trong thread.
Spec: [mục 9](../Running-Project-Specification-Student.md).

## Kiến trúc

```
Slack ──HTTP──> /slack/events ─(1) raw body ─(2) verify HMAC v0 + timestamp ≤5' ─(3) parse / challenge
                                  │ 401 nếu sai                           │
                                  └──────────── (4) enqueue SQLite, dedup event_id ──> ACK 200 (<3s)
worker nền ─ claim (lease) ─> route intent ─> PermissionPolicy ─┬ read  ─> MCP kubernetes / prometheus
                                                                ├ write ─> token 60s ─confirm─> kubectl scale (SA chatops-scaler)
                                                                └ destructive ─> denied
             audit JSONL (mọi quyết định + mọi tool call) ─> chat.postMessage (thread)
```

| Thành phần | File |
|---|---|
| HTTP transport, ACK | `app/main.py` |
| Signature + replay (≤300s) | `app/signature.py` |
| Durable queue, dedup, retry bounded (3), lease | `app/queue.py`, `app/worker.py` |
| Intent router (VI/EN) | `app/intents.py` |
| 3 intent + enriched context | `app/skills.py` |
| MCP client (stdio, allowlist tool) | `app/mcp_client.py`, `mcp-servers.json` |
| 3-tier permission, approval token | `app/permissions.py`, `service-catalog.yaml` |
| Mutation identity riêng | `app/executor.py`, `k8s/rbac.yaml` |
| Audit JSONL | `app/audit.py` → `chatops-audit.log` |
| LLM tool loop (tuỳ chọn, chỉ tool đọc) | `app/llm.py`, `prompts/system.md` |

Intent → tool call:

| Câu hỏi | MCP tool |
|---|---|
| "InsightHub có healthy không?" | `kubernetes.resources_list` (Deployment) + `prometheus_query` `up{}` + `prometheus_query` 5xx ratio → **3 call, nhiều nhất** |
| "Hôm nay ingest bao nhiêu doc?" | `prometheus_query` `increase(insighthub_worker_jobs_total[từ 00:00])` + backlog |
| "Pod nào đang lỗi?" | `kubernetes.pods_list_in_namespace` + `kubernetes.events_list` (chỉ khi có pod lỗi) |
| "scale api to 3" | không gọi MCP; hỏi confirm token → `kubectl scale` bằng SA `chatops-scaler` |

Permission: **read** tự động · **write** (scale 3 Deployment trong catalog, 1–5 replicas) chỉ `CHATOPS_OPERATOR_IDS`, bắt buộc token 60s dùng 1 lần, bind action+args+channel · **destructive** (delete/drain/rollback/exec/rollout restart…) luôn từ chối. Action lạ mặc định là destructive.

## Chạy local (Mac + minikube)

Điều kiện: Day 4 stack đang chạy (namespace `insighthub-local`, Prometheus port-forward `9090`), Node (npx), kubectl, ngrok.

```bash
cd ~/Downloads/DO2603/insighthub
python3.12 -m venv .venv-bot && . .venv-bot/bin/activate   # lock sinh cho Python 3.12 (>=3.11)
pip install --require-hashes -r chatops-bot/requirements-dev.txt

# 1. Identity K8s: read-only cho MCP, scaler riêng cho mutation
kubectl apply -f chatops-bot/k8s/rbac.yaml
bash chatops-bot/k8s/make-kubeconfig.sh chatops-readonly ~/.kube/chatops-readonly.kubeconfig
bash chatops-bot/k8s/make-kubeconfig.sh chatops-scaler   ~/.kube/chatops-scaler.kubeconfig
kubectl auth can-i delete pods -n insighthub-local --as=system:serviceaccount:insighthub-local:chatops-readonly   # no
kubectl --kubeconfig ~/.kube/chatops-scaler.kubeconfig auth can-i patch deployments/insighthub-api --subresource=scale   # yes
kubectl --kubeconfig ~/.kube/chatops-scaler.kubeconfig auth can-i list pods   # no

# 2. Test
pytest chatops-bot/tests/ -q

# 3. Chạy bot + ngrok (2 tab)
cp chatops-bot/.env.example chatops-bot/.env   # điền secret
cd chatops-bot && set -a && source .env && set +a && uvicorn app.main:app --port 8080
ngrok http 8080
```

Slack App: api.slack.com/apps → *From an app manifest* → dán `slack-app-manifest.yaml` (thay `<NGROK_URL>`) → Install to Workspace → lấy *Bot User OAuth Token* (`xoxb-…`) và *Signing Secret* (Basic Information) → điền `.env` → restart uvicorn → Event Subscriptions phải hiện **Verified** → `/invite @insighthub-ops` vào channel. `SLACK_BOT_USER_ID`: click profile bot → Copy member ID.

Kiểm chứng:

```bash
curl -s localhost:8080/healthz
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8080/slack/events -d '{"type":"url_verification","challenge":"x"}'   # 401
tail -f chatops-bot/chatops-audit.log
```

Trong Slack: `@insighthub-ops InsightHub có healthy không?` · `@insighthub-ops Hôm nay ingest bao nhiêu doc?` · `@insighthub-ops Pod nào đang lỗi?` · `@insighthub-ops scale api to 3` → `confirm <token>` hoặc nút Approve · (tài khoản không phải operator) `scale api to 3` → bị từ chối · `delete pod ...` → bị từ chối.

## Evidence

```bash
python3 chatops-bot/tools/export_evidence.py --since <RFC3339 lúc bắt đầu demo>
python3 scripts/verify.py day5 --evidence-dir evidence --bot-url http://localhost:8080
```

## Giới hạn đã biết

- Queue SQLite + ApprovalStore in-memory: đúng cho 1 replica. Nhiều replica cần Redis/SQS và store token dùng chung; restart bot làm mọi token đang chờ mất hiệu lực (fail-safe).
- Worker đã post reply nhưng crash trước `complete()` thì Slack có thể nhận 2 reply (at-least-once).
- Dockerfile chưa có Node nên MCP stdio chỉ chạy khi bot chạy trên host; deploy K8s cần image có Node hoặc MCP server dạng HTTP.
