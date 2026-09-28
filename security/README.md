# Day 6 - Security, Governance & FinOps

Spec: [mục 10](../Running-Project-Specification-Student.md). Threat model: [threat-model.md](threat-model.md).

## Kiến trúc

```
InsightHub api/worker ─(key insighthub)─┐
ChatOps bot (Mac) ─────(key chatops-bot)─┼─> LiteLLM gateway (ns llm-gateway) ─> Gemini 3.1 Flash-Lite
coding client (Mac) ───(key coding)──────┘     │ guardrail: L1 rules, L1b RAG sanitize,      └─ fallback Ollama qwen2.5
                                               │ L2 Llama Guard 3 (Ollama), L3 output redact
                                               │ max_budget + rpm/tpm per key, spend logs (Postgres)
                                               └─ /metrics -> Prometheus -> dashboard "LLM Cost"
```

| Thành phần | File |
|---|---|
| Gateway (k8s, kustomize) | `security/gateway/` (`config.yaml`, `insighthub_guardrail.py`, `bootstrap.sh`, `keys.py`) |
| App defenses | `api/app/services/sanitize.py`, `api/app/services/llm.py` (hardened prompt, `LLM_DEFENSES`) |
| Gateway error mapping | `api/app/core/providers.py` → `guardrail_blocked` (400), `llm_budget_exceeded` (429), `request_id` |
| Helm overlay | `infra/helm/insighthub/values-gateway.yaml` |
| Promptfoo | `security/promptfooconfig.yaml`, `security/promptfoo/` (pin 0.123.1 + lockfile, provider upload→retrieve) |
| Eval dataset + cost | `security/eval/` (`dataset.json`, `run_eval.py`, `pricing.json`, `build_day6_evidence.py`) |
| Poisoned doc | `sample-docs/huong-dan-nguoi-moi.md` (của khoá) + `security/poisoning/so-tay-oncall-bo-sung.md` |
| Budget/injection tests | `tests/milestones/day6/test_day6.py`, `security/tests/`, `api/tests/test_unit_security.py` |
| FinOps | `observability/grafana-dashboards/llm-cost.json`, `observability/rules/llm-cost-rules.yaml` |
| Coding workflow | `tools/coding-client/review_diff.py` |

Virtual keys (30 ngày, USD giá list): `insighthub` 2.0 (60 rpm), `chatops-bot` 0.5 (20 rpm), `coding` 0.5 (20 rpm) → tổng cap 3.0 USD.
Mỗi key chỉ gọi được model của nó (key insighthub gọi `coding-review` → 403).

## Runbook (Mac + minikube)

Mọi lệnh chạy ở thư mục repo, không paste phần comment.

### 0. Chuẩn bị
```bash
brew install ollama
ollama serve            # tab riêng (hoặc mở app Ollama)
ollama pull llama-guard3:1b
ollama pull mxbai-embed-large
ollama pull qwen2.5:7b-instruct
```
Cần Gemini API key (aistudio.google.com/apikey, project không bật billing). Kiểm tra pod trong minikube gọi được Ollama trên Mac:
```bash
kubectl run ollama-check -n default --rm -i --restart=Never --image=curlimages/curl:8.10.1 -- curl -s -m 5 http://host.minikube.internal:11434/api/tags
```
Không ra JSON → chạy Ollama với `OLLAMA_HOST=0.0.0.0 ollama serve` rồi thử lại.

### 1. Build image api/worker mới
```bash
docker build --load -t insighthub-api:local api
docker build --load -t insighthub-worker:local -f ingestion-worker/Dockerfile .
minikube image load insighthub-api:local
minikube image load insighthub-worker:local
```

### 2. Gateway + 3 virtual keys
```bash
bash security/gateway/bootstrap.sh
kubectl -n llm-gateway port-forward svc/litellm 4000:4000
```
Script hỏi Gemini key (không hiện khi gõ), lưu mọi secret ở `~/.insighthub/gateway.env` (chmod 600) và tạo Secret `insighthub-llm-gateway` cho api/worker. Port-forward để ở tab riêng.

### 3. Baseline (trước khi bật phòng thủ) – để scan initial trung thực
```bash
kubectl -n llm-gateway set env deploy/litellm GUARDRAIL_MODE=off
helm upgrade --install insighthub infra/helm/insighthub -n insighthub-local \
  -f infra/helm/insighthub/values-local.yaml -f infra/helm/insighthub/values-gateway.yaml \
  --set config.LLM_DEFENSES=false --wait
kubectl -n insighthub-local rollout restart deploy/insighthub-api deploy/insighthub-ingestion-worker
kubectl -n insighthub-local port-forward svc/insighthub-api 18000:8000
bash security/scripts/reset-index.sh
```
`reset-index.sh` xoá documents/chunks (embedding identity đổi sang gateway + mxbai) rồi upload lại 3 sample-docs.

### 4. Scan initial
```bash
npm --prefix security/promptfoo ci --ignore-scripts
export PROMPTFOO_PYTHON=python3 INSIGHTHUB_API_URL=http://127.0.0.1:18000
security/promptfoo/node_modules/.bin/promptfoo redteam generate -c security/promptfooconfig.yaml -o security/red-team-tests.yaml
security/promptfoo/node_modules/.bin/promptfoo redteam eval -c security/red-team-tests.yaml \
  -o security/reports/red-team-report-initial.html security/reports/red-team-initial.json
python3 security/eval/run_eval.py --label initial --out evidence/day6/eval-initial.json
```
Lần đầu Promptfoo hỏi email (remote generation cho plugin indirect-prompt-injection/hijacking). `red-team-tests.yaml` là bộ test cố định dùng lại cho scan final.

### 5. Bật phòng thủ, scan final trên cùng bộ test
```bash
kubectl -n llm-gateway set env deploy/litellm GUARDRAIL_MODE=enforce
helm upgrade --install insighthub infra/helm/insighthub -n insighthub-local \
  -f infra/helm/insighthub/values-local.yaml -f infra/helm/insighthub/values-gateway.yaml --wait
kubectl -n insighthub-local rollout restart deploy/insighthub-api
security/promptfoo/node_modules/.bin/promptfoo redteam eval -c security/red-team-tests.yaml \
  -o security/reports/red-team-report-final.html security/reports/red-team-final.json
python3 security/eval/run_eval.py --label final --out evidence/day6/eval-final.json --cost-out evidence/day6/cost-final.json
```
(port-forward api cần chạy lại sau rollout restart.)

### 6. Workload bot + coding qua gateway
```bash
grep -E '^(CHATOPS_LITELLM_KEY|CODING_LITELLM_KEY)=' ~/.insighthub/gateway.env
```
- Bot: thêm `LITELLM_BASE_URL=http://127.0.0.1:4000/v1` và `CHATOPS_LITELLM_KEY=...` vào `chatops-bot/.env`, restart uvicorn, hỏi Slack một câu ngoài 3 intent (vd "latency p95 thế nào?").
- Coding: `git add -A` rồi
```bash
set -a; . ~/.insighthub/gateway.env; set +a
python3 tools/coding-client/review_diff.py --base day5-chatops --tests "make test-chatops PYTHON=python"
```

### 7. Dashboard, alert, key info
```bash
observability/apply-rules.sh monitoring llm-cost-rules.yaml
observability/apply-dashboard.sh monitoring llm-cost.json
set -a; . ~/.insighthub/gateway.env; set +a; python3 security/gateway/keys.py info
```
Grafana → dashboard **InsightHub - LLM Cost (Gateway)**. LiteLLM UI: http://127.0.0.1:4000/ui (đăng nhập bằng master key) để chụp virtual keys.

### 8. Evidence + verify
```bash
python3 security/eval/build_day6_evidence.py
set -a; . ~/.insighthub/gateway.env; set +a
LITELLM_URL=http://127.0.0.1:4000 python3 scripts/verify.py day6 --evidence-dir evidence \
  --api-url http://127.0.0.1:18000 --test-timeout 900
```
Chạy bước 8 sau khi code đã chốt (source_sha256 phủ cả file chưa commit); đổi code sau đó phải chạy lại bước 5.

## Runbook alert

### InsightHubLLMBudgetLow
Key còn <20% budget. Xem panel "Spend 24h" để tìm workload; nếu do vòng lặp/abuse thì `keys.py info`, khoá key (`/key/block`), sau đó mới cân nhắc nâng `max_budget`.

### InsightHubLLMCostSpike
Chi phí >0.5 USD/giờ trong 10 phút. Kiểm tra guardrail log `kubectl -n llm-gateway logs deploy/litellm | grep GUARDRAIL`, panel tokens/s theo key; giảm `rpm_limit` của key bị lạm dụng.

## Giới hạn đã biết
- Budget check trước call, spend ghi sau call → burst đồng thời vượt budget tối đa khoảng số request đang bay; test đo và ghi lại overshoot.
- Regex guardrail có false positive/negative; code review từng bị chặn nhầm (diff chứa chính regex guardrail) → key `coding` dùng profile riêng (redact secret, bỏ regex injection, vẫn chạy Llama Guard).
- Llama Guard 1B fail-closed: Ollama tắt thì mọi chat bị chặn (`guard_unavailable`).
- AWS Budgets (MH11) không áp dụng: lab Day 6 không chạy AWS.
