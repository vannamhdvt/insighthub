# Day 1 - AI prompt log

Implementation do agent viết. Việc của tôi là chốt phạm vi, kiểm chứng, và bác bỏ
phần agent làm quá hoặc làm sai. Các mục dưới ghi đúng những gì đã xảy ra.

## Môi trường

| Mục | Giá trị |
| --- | --- |
| Coding host | Claude Code |
| Model / auth | claude-opus-5 / subscription |
| OS | macOS, Node v25.9.0 |
| Checkout | `~/Downloads/DO2603/insighthub`, branch `day1-async-ingestion` |
| Context | `AGENTS.md` 194 dòng, 6 section |
| Adapter | `CLAUDE.md` chỉ `@AGENTS.md`, không copy nội dung |
| MCP config | `.mcp.json`, sha256 `1db45648983a4b60ebfa28a51d571bee7636c9750a235f779f13951d9ac0e8a2` |

Không có token hay secret trong file này.

---

## Prompt 1 - Đọc verifier trước khi code

**Ràng buộc nêu trước:** không đổi `infra/db/init.sql`, không sửa thân
`process_document()`, không thêm endpoint ngoài contract.

**Prompt:**

> Đọc `scripts/verify.py` (hàm `day1`, `run_tests`, `smoke`) và
> `scripts/VERIFICATION_CONTRACT.md`, rồi liệt kê chính xác: artifact role bắt buộc,
> tên test bắt buộc và vị trí file test, format log mà verifier grep, schema của
> `evidence/day1.json`. Chỉ trả lời từ nội dung file, không suy đoán.

**Agent trả về:** 4 artifact role (`refactor`, `review`, `worker`,
`worker_dockerfile`); 6 test bắt buộc tại `tests/milestones/day1/`; log JSON một dòng
với `event="ingestion_completed"` + `document_id` + `status="ready"` + timestamp
RFC3339; `source_sha256` phủ cả file chưa commit; `mode` phải là `real`.

**Quyết định:** giữ thứ tự này cho các ngày sau. `tests/milestones/day1/` là thư mục
khác hẳn `api/tests/` và 6 tên test là bắt buộc — code trước thì phải làm lại.

---

## Prompt 2 - Refactor upload sang 202

**Ràng buộc nêu trước:** API không chạy ingestion; DB call là blocking nên phải chạy
ngoài event loop; Redis không chứa nội dung tài liệu.

**Prompt:**

> Refactor `POST /documents` sang 202: stage bytes vào volume dùng chung, ghi row
> `pending` kèm `content_sha256`, enqueue ARQ job, trả về ngay. Không đổi
> `process_document()`. Mọi lời gọi DB qua `run_in_threadpool`. Enqueue thất bại thì
> ghi `failed`, không treo `pending`.

**Agent đề xuất:** truyền bytes tài liệu trực tiếp trong payload job.

**Bác bỏ:** 10MB mỗi upload vào Redis là biến queue thành content store, và Day 4
phải đo backlog queue. Thay bằng `app/core/staging.py` ghi ra volume, worker verify
digest trước khi ingest.

**Agent bỏ sót:** volume mount vào container non-root sẽ do root sở hữu. Phải thêm
`mkdir` + `chown` trong `api/Dockerfile` trước `USER appuser`. Chỉ lộ ra khi chạy
Docker thật, đọc code không thấy.

---

## Prompt 3 - Worker và structured log

**Ràng buộc nêu trước:** worker không tự ghi/xoá chunks; log JSON một dòng ra stdout;
không log nội dung tài liệu hay DSN.

**Prompt:**

> Viết ARQ worker gọi `process_document()` qua `asyncio.to_thread`, đọc staged bytes
> và verify digest trước. Phát event `ingestion_completed` kèm `document_id`,
> `status`, timestamp RFC3339. Job thất bại thì ghi nhận rồi return.

**Agent đề xuất:** raise lại exception để ARQ tự retry theo `max_tries`.

**Bác bỏ:** `process_document()` đã commit `failed` với `error_code` đúng, raise chỉ
khiến ARQ chạy lại và ghi lại cùng một thất bại. Worker return kết quả.

**Quyết định về phạm vi:** agent tự thêm retry 3× exponential backoff, upload dedup,
YAML anchor và một script sinh evidence. Tôi cắt hết vì đó là Should-have, không phải
Must-have Day 1. Chấp nhận trần điểm L3 để bài đúng phạm vi và tôi giải thích được
từng phần.

---

## Prompt 4 - Cập nhật test theo contract mới

**Ràng buộc nêu trước:** đổi contract không phải quyền xoá test; giữ toàn bộ assertion
về idempotency, atomicity, identity và provider failure.

**Prompt:**

> Cập nhật `api/tests` từ sync 201 sang async 202 + bước worker tường minh. Không xoá
> assertion nào; chỉ chuyển điểm quan sát từ HTTP response sang document state.

**Kết quả:** thêm `queue_stub()` và `run_worker_once()` vào `api/tests/support.py`.
`test_provider_failure_is_502...` đổi tên thành
`..._is_recorded_by_the_worker_and_metadata_truthful` vì 502 không còn là response của
upload. `test_file_read_is_bounded_and_closed` phải bọc `asyncio.run()` vì handler đổi
sang `async def`.

**Quyết định:** đồng ý đổi tên, vì tên cũ nói sai về hành vi. Kiểm assertion bên
trong không bị nới: `("failed", 0, "provider_error")` vẫn nguyên.

---

## Bác bỏ tài liệu do agent viết

**Bản `AGENTS.md` đầu tiên:** đủ 6 section và đúng luật nhưng tôi đọc không hiểu —
câu dài nhiều mệnh đề, thuật ngữ không giải thích, trộn quy tắc với lý do. Yêu cầu
viết lại: mỗi dòng một ý, mở ngoặc giải thích thuật ngữ lần đầu xuất hiện. Một context
file mà tôi không giải thích được khi bị hỏi thì vô dụng dù đủ mục để ăn điểm.

**Cấu trúc `AGENTS.md`:** bản viết lại vẫn nhắc "Day 1" ở nhiều chỗ, tức là viết như
nhật ký từng ngày. Yêu cầu chuyển sang thời hiện tại, mô tả hệ thống như nó đang là,
và tách ràng buộc tạm theo kỳ ra mục riêng. Nếu không, tới Day 4 file thành changelog
và hết chỗ trong 200 dòng.

---

## Kiểm chứng đã chạy

| Lệnh | Kết quả |
| --- | --- |
| `docker compose config --services \| wc -l` | 5 |
| `docker compose ps` | 5 service Running, đều healthy |
| `time curl -X POST localhost:8000/documents ...` | `202` trong **0.057s** |
| `curl -s localhost:8000/documents` | `pending` → `ready`, `chunk_count: 1`, `error_code: null` |
| `docker compose logs ingestion-worker` | `event=ingestion_completed`, `status=ready`, timestamp RFC3339 |
| `make test-backend` | 49 tests OK, integration chạy thật với PostgreSQL (`RUN_DB_TESTS=1`) |
| `make smoke` | PASS, `runtime_verified=true` |
| `ruff check api ingestion-worker tests/milestones` | clean |
| `scripts/verify.py day1 --evidence-dir evidence` | **PASS**, `runtime_verified=true` |

Hai lỗi chỉ lộ ra khi chạy thật, không phát hiện được bằng đọc code:

* `test_refactor_regression` dùng `re.search` khớp cả chuỗi `process_document()` nằm
  trong docstring. Sửa sang parse `ast` để chỉ tìm lời gọi thật.
* `.mcp.json` nằm trong source digest, nên sinh evidence trước khi sửa file đó là vô
  nghĩa — verifier báo `Evidence source digest differs`.

---

## MCP setup

| Mục | Giá trị |
| --- | --- |
| Server | `insighthub-readonly`, stdio, `tools/mcp/src/server.mjs` |
| SDK | `@modelcontextprotocol/server@2.0.0` |
| Tool allowlist | `insighthub_health`, `insighthub_list_documents` |
| `prometheus_summary` | tắt qua `INSIGHTHUB_MCP_PROMETHEUS=0` |
| Trạng thái trong host | `/mcp` → `insighthub-readonly · connected · 2 tools` |
| `npm --prefix tools/mcp test` | 22/22 pass |
| `smoke.mjs` | `passed=true`, `backend_mode=fixture`, `live=false` |
| `smoke.mjs --live` | `passed=true`, `backend_mode=live`, `live=true` |
| `check-agent-setup.py` | `PASS`, `context_contains_todo=false`, `milestone_complete=false` |

Protocol thực tế trên wire, không suy đoán từ version SDK: client 2.0.0 mode
`modern`/`auto` chạy `2026-07-28` với `server/discover`; mode `legacy` và SDK 1.30.0
chạy `2025-11-25` với `initialize`.

Allowlist thực thi ở server, không ở prompt: smoke fixture gọi được 3 tool kể cả
`prometheus_summary`, smoke live chỉ gọi 2. Tool ngoài allowlist không được đăng ký và
gọi thẳng `tools/call` vẫn bị chặn.

**Quyết định:** đổi `command` từ `/opt/homebrew/Cellar/node/25.9.0_1/bin/node` sang
`/opt/homebrew/bin/node` vì bản Cellar gắn cứng version, `brew upgrade node` là server
chết. Chọn "Use this MCP server" chứ không "Use this and all future MCP servers", để
Day 2 mỗi backend vẫn phải approve tường minh.

### Tool call thật trong host

Prompt: *"Gọi tool `insighthub_health`, rồi gọi `insighthub_list_documents` với
`{"limit":2}`. In nguyên văn JSON trả về, không diễn giải."*

```json
{"live":true,"ready":true,"databaseReady":true}
```

```json
{"documents":[{"id":9,"status":"ready","chunk_count":1},{"id":8,"status":"ready","chunk_count":1}],"returned":2,"truncated":true}
```

`truncated=true` đúng vì `limit=2` mà index có nhiều document hơn. Projection chỉ trả
`id`, `status`, `chunk_count` — không có filename hay nội dung tài liệu.

SDK smoke pass không đồng nghĩa host gọi được tool. Đây là hai bằng chứng riêng.
