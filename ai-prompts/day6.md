# Day 6 - AI prompt log

## Môi trường

| Mục | Giá trị |
| --- | --- |
| Coding host | Claude (Cowork) viết code + test; chạy thật trên Mac (minikube + Ollama) |
| Branch | `day6-security` (từ `day5-chatops`) |
| LLM upstream | Gemini 3.1 Flash-Lite (free tier, cost tính theo giá list $0.25/$1.5 per 1M) qua LiteLLM v1.103.0 |
| Local models | Ollama: `llama-guard3:1b` (guardrail L2), `mxbai-embed-large` (embedding), `qwen2.5:7b-instruct` (fallback) |
| Red team | Promptfoo 0.123.1 (pin + lockfile) |

---

## Prompt 1 - Chọn provider và kiến trúc gateway

**Prompt:** "làm day 6" + trả lời: chỉ có Claude subscription (không có API key) → dùng Gemini free key; Mac M2 Pro 32GB; guardrail tự viết + Llama Guard 3; coding workflow bằng script client.

**AI đề xuất / quyết định:**
- Gateway chạy trong minikube (ns `llm-gateway`) với Postgres PVC (budget cần DB, không SQLite; emptyDir sẽ mất key khi restart).
- InsightHub không cần sửa luồng provider: dùng sẵn provider `openai` (OpenAI-compatible) trỏ vào gateway, key = virtual key. Embedding cũng qua gateway (`mxbai-embed-large` trên Ollama) để không còn đường bypass.
- Giá đặt tường minh trong `config.yaml` (`input_cost_per_token`) để spend/budget tính được kể cả khi dùng free tier.
- Kiểm tra trước trong source LiteLLM 1.103: Prometheus callback là OSS (không còn premium check) nhưng `/metrics` mặc định **cần auth** → chọn `require_auth_for_metrics_endpoint: false` + NetworkPolicy thay vì nhét master key vào scrape config.

**Bác bỏ:** chạy LiteLLM bằng docker compose trên Mac (pod InsightHub phải gọi ra host, DNS không chắc chắn); dùng Bedrock Guardrails (không chạy AWS).

**Verify:** dựng LiteLLM 1.103.0 thật (pip) + Postgres + upstream giả trong sandbox: key model access 403, guardrail chặn/sanitize, output redact, budget 429, metrics có `api_key_alias`.

**Review cá nhân:**
- Đúng: budget chặn bằng HTTP **429** `budget_exceeded` (không phải 400 như tưởng) → mapping ở API dựa vào marker `budget_exceeded`, không dựa status.
- Spend được enforce từ cache ngay nhưng ghi DB theo batch (`proxy_batch_write_at`) → `/key/info` trả 0 vài giây đầu; test phải chờ, không assert ngay.

---

## Prompt 2 - Guardrail nhiều lớp + sanitize context

**AI đề xuất / quyết định:** guardrail custom (hook native của LiteLLM): L1 regex direct injection/secret, L1b strip đoạn có dạng chỉ dẫn trong `documents` của payload InsightHub, L2 Llama Guard 3 (fail-closed), L3 redact PII/secret + chặn rò system prompt/canary. Ở app thêm `sanitize.py` + hardened prompt, bật/tắt bằng `LLM_DEFENSES` để scan initial tái hiện đúng hành vi Day 5 trên cùng source (verifier yêu cầu initial/final cùng `source_sha256`).

**Verify:** `security/tests` (21), `api/tests/test_unit_security.py`, chạy thật qua gateway sandbox.

**Review cá nhân:**
- Bug phát hiện khi test với poisoned doc của khoá (`sample-docs/huong-dan-nguoi-moi.md`): chỉ dẫn trải trên nhiều dòng ("NOTE FOR THE AI ASSISTANT… / …respond only with…"), lọc theo từng dòng bỏ sót dòng giữa → đổi sang lọc theo **đoạn** (paragraph) + thêm pattern `note for the ai`, `maintenance mode`, `respond only`.
- False positive thật: coding client gửi diff có chứa chính regex guardrail và câu hardened prompt → bị chặn `direct_injection`. Sửa: profile `GUARDRAIL_CODE_KEYS=coding` (bỏ regex injection, redact secret thay vì chặn, Llama Guard vẫn chạy) + test cho cả hai phía.
- Đánh đổi: lọc theo đoạn có thể xoá luôn câu hợp lệ nằm chung đoạn (vd khối "Liên hệ" bị xoá vì câu "không gửi mật khẩu") — chấp nhận vì an toàn hơn, ghi ở threat model.

---

## Prompt 3 - Red team, eval, budget test

**AI đề xuất / quyết định:**
- Promptfoo: plugin `indirect-prompt-injection` với provider Python tự viết **upload payload thành tài liệu thật** → ingest → retrieve → `/chat`, xoá tài liệu sau mỗi case; `jailbreak-templates` thay cho strategy cũ `prompt-injection`; `rag-poisoning` không dùng vì cần remote KB, RAG poisoning phủ bằng đường upload thật + poisoned doc cố định.
- Dataset chuẩn hoá 20 case (injection trực tiếp/gián tiếp, PII, excessive agency, benign) chạy qua API thật; cost = token × giá list, case bị guardrail chặn có `resource_usage` đo được (wall clock + dung lượng Llama Guard từ Ollama `/api/ps`).
- `test_budget_enforced`: key tạm budget 0.00005 USD → tuần tự phải có 200 rồi 429; burst 8 request song song để **đo** overshoot; xoá key sau test.

**Verify:** `scripts/verify.py day6` PASS trong sandbox với upstream giả (chỉ để kiểm tra contract; evidence thật phải chạy lại trên Mac, đã xoá dữ liệu giả).

**Review cá nhân:**
- Burst test trên sandbox: 1/8 request thành công, 7 bị 429 → overshoot nhỏ hơn dự kiến; con số thật với Gemini ghi ở phần kết quả bên dưới.

---

## Kết quả chạy thật (điền sau khi chạy runbook `security/README.md`)

| | Initial (LLM_DEFENSES=false, GUARDRAIL_MODE=off) | Final (defenses on) |
|---|---|---|
| Promptfoo: số test / fail / HIGH fail | 97 / 64 fail (3 error) / 51 HIGH fail (trên 73 HIGH), 16 medium fail (trên 24 medium); không có plugin severity critical trong bộ này | 97 / 4 fail (3 error) / cải thiện từ 64→4 fail |
| Eval dataset (20 case) passed | 13-16/20 (dao động nhẹ giữa các lần chạy do model non-deterministic; lần chốt cuối: 14/20) | **20/20** (lần chốt cuối, sau fix off-topic refusal) |
| Chi phí eval (USD) | ~0.0098-0.0104 | ~0.0068-0.0073 |

- Finding initial đáng chú ý: guardrail off để lộ toàn bộ injected instruction trong poisoned doc
  (canary `IH-CANARY-7731`, link phishing `evil.example`, giả mạo yêu cầu gửi mật khẩu qua email/SĐT),
  PII (SĐT/email) không được che, và model chiều theo yêu cầu sáng tác off-topic (thơ bóng đá) dù đây
  là dấu hiệu hijacking.
- Fix iteration sau scan initial (commit, chưa push):
  1. Llama Guard timeout quá ngắn (8s) gây fail-closed 100% khi Ollama cold-load → thêm
     `LLAMA_GUARD_TIMEOUT`/`LLAMA_GUARD_KEEP_ALIVE` cấu hình được.
  2. Chunking word-based phá hết newline → sanitize/guardrail lọc theo đoạn bị lem sang câu hợp lệ
     chung chunk → đổi sang lọc theo **câu** (sentence-split) ở cả `sanitize.py` và
     `insighthub_guardrail.py`.
  3. Regex injection bỏ sót câu ép trả lời cố định không có danh từ actor ("luôn trả lời chính xác:
     ...") → thêm pattern actor-less.
  4. False positive: regex "gửi ... mật khẩu" khớp cả câu phủ định ("không gửi mật khẩu qua Slack")
     → thêm negative lookbehind theo phủ định tiếng Việt.
  5. Model vẫn chiều yêu cầu sáng tác off-topic (thơ bóng đá) dù có rule (5) trong hardened prompt
     → làm rõ rule: liệt kê tường minh các dạng sáng tác (thơ/truyện/bài hát/code không liên quan)
     và cấm thực hiện dù chỉ một phần.
- Budget test: sequential / burst overshoot: **chưa verify được bằng live run** — model `coding-review`
  không có fallback Ollama, và quota free-tier Gemini (500 req/ngày) đã cạn do chạy nhiều vòng
  scan/eval trong ngày 2026-09-28 (xem `security/threat-model.md`). Logic budget/virtual-key (tạo key,
  chặn 429, cost tracking) đã được verify gián tiếp qua eval final chạy thật 20/20 case với cost
  report hợp lệ; `test_budget_enforced` cần chạy lại sau khi quota reset hoặc sau khi thêm fallback
  cho `coding-review`.
