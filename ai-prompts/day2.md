# Day 2 - AI prompt log

## Môi trường

| Mục | Giá trị |
| --- | --- |
| Coding host | Claude Code |
| OS | macOS, Docker Desktop, minikube v1.37.0 |
| Checkout | `~/Downloads/DO2603/insighthub`, branch `day1-async-ingestion` |
| MCP config | `.mcp.json` — 5 server (insighthub-readonly + 4 backend mới) |
| K8s cluster | minikube, namespace `insighthub` |
| ServiceAccount | `mcp-readonly`, ClusterRole read-only (get/list/watch) |

---

## Prompt 1 - Cấu hình 4 backend MCP

**Ràng buộc nêu trước:** pin cứng version (không `@latest`); filesystem allow-list
chỉ project dir; K8s dùng kubeconfig riêng của ServiceAccount `mcp-readonly`,
không dùng kubeconfig admin.

**Việc làm:** thêm 4 entry vào `.mcp.json`: `@modelcontextprotocol/server-filesystem@2026.8.31`,
`docker-mcp==0.2.0` (qua uvx), `kubernetes-mcp-server@0.0.67 --read-only`,
`prometheus-mcp@1.1.3`. Approve từng server tường minh qua host (không chọn
"and all future").

**Kết quả:** 5/5 connected, gọi tool thật trên cả 4 backend (list_directory,
list-containers, pods_list_in_namespace, prometheus_query "up") — có trace
input/output/timestamp.

**Review cá nhân:** config đúng ngay lần đầu, không phải approve thủ công
từng server. Dễ bỏ sót nhất là thứ tự: phải tạo RBAC (ServiceAccount +
ClusterRole) trước khi approve K8s MCP, không thì server vẫn connect được
nhưng call gì cũng Forbidden.

---

## Prompt 2 - Fix docker-mcp dependency drift

**Vấn đề:** `uvx docker-mcp==0.2.0` crash `AttributeError: 'Server' object
has no attribute 'list_prompts'`.

**Root cause:** package `docker-mcp` không pin cứng dependency `mcp` SDK
trong `pyproject.toml`, nên `uv` kéo bản `mcp` mới nhất — bản mới đổi API
(`list_prompts` decorator không còn tồn tại ở dạng cũ).

**Quyết định:** ép version SDK tương thích thay vì đổi sang package khác —
`uvx --with "mcp<1.9" docker-mcp==0.2.0`. Verify: server chạy được, connect
được qua host.

**Review cá nhân:** ban đầu tưởng do mình cấu hình sai, mất một lúc đọc lại
`.mcp.json` mới ra là do `docker-mcp` không pin dependency `mcp` SDK. Rút
kinh nghiệm: pin version ở CLI thôi chưa đủ, phải check luôn dependency bên
trong package.

---

## Prompt 3 - Debug session: ingestion-worker crash

**Setup case:** đổi tạm `REDIS_URL` sang `redis://wrong-host:6379/0`,
`docker compose up -d --force-recreate ingestion-worker` để mô phỏng lỗi
config sai host.

**Debug qua Docker MCP (không dùng docker CLI trực tiếp):** `list-containers`
xác nhận container `exited`; `get-logs` cho thấy `socket.gaierror: Name or
service not known` → `redis.exceptions.ConnectionError` → ARQ worker raise
và exit sau 5 lần retry.

**Root cause:** `REDIS_URL` trỏ sai hostname, không khớp tên service `redis`
trong `docker-compose.yml`. Không phải Redis chết (container `redis` vẫn
`running` suốt).

**Fix:** revert `REDIS_URL`, `docker compose up -d --force-recreate
ingestion-worker`, verify `Up (healthy)`. Ghi lại toàn bộ vào
`debug-session-day2.md`.

**Review cá nhân:** `docker compose ps` mặc định không hiện container đã
exited, nên lúc đầu tưởng nhầm Redis chết. Phải `get-logs` qua MCP mới ra
đúng nguyên nhân là DNS fail. Bắt buộc debug qua MCP tool thay vì docker CLI
tay cũng có cái hay — có sẵn trace để xem lại.

---

## Kiểm chứng đã chạy

| Lệnh | Kết quả |
| --- | --- |
| `kubectl get sa mcp-readonly -n insighthub` | tồn tại |
| `kubectl auth can-i get pods --as=...mcp-readonly` | yes |
| `kubectl auth can-i delete pods --as=...mcp-readonly` | no |
| `claude mcp list` | 5/5 project MCP connected |
| Tool-call thật 4 backend | có trace, evidence lưu qua host session |
| MCP Inspector (4 server độc lập) | 4 screenshot `evidence/mcp/inspector/` |
| `python3 scripts/verify.py day2 --evidence-dir evidence ...` | **PASS**, `runtime_verified=true` |
