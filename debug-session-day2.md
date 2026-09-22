# Debug session Day 2 — ingestion-worker crash (Docker MCP)

## Setup case
Đổi tạm `REDIS_URL` trong `.env` từ `redis://redis:6379/0` sang
`redis://wrong-host:6379/0`, `docker compose up -d --force-recreate ingestion-worker`
để mô phỏng lỗi config sai host — đúng pitfall AGENTS.md đã cảnh báo
("api và ingestion-worker phải dùng chung REDIS_URL... Lệch một trong hai
là upload trả 202 mà không ai xử lý").

## Symptom
`docker compose ps` (mặc định chỉ hiện container đang chạy) không còn thấy
`ingestion-worker` — biến mất khỏi list vì đã exit.

## Debug qua Docker MCP (trong host, không dùng docker CLI trực tiếp)

**Bước 1 — `list-containers`:**
Container `insighthub-do2603-ingestion-worker-1` status `exited`, các
service khác (redis, postgres, api, web) vẫn `running`.

**Bước 2 — `get-logs` trên container đó:**
```
14:04:28-14:04:33: redis connection error wrong-host:6379
  ConnectionError Error -2 connecting to wrong-host:6379.
  Name or service not known., [5→1] retries remaining
Traceback: socket.gaierror: [Errno -2] Name or service not known
  -> redis.exceptions.ConnectionError
  -> arq.connections.create_pool() raise, worker exit
```

## Root cause
`ingestion-worker` không resolve được hostname `wrong-host` (DNS lookup
thất bại, không phải do Redis service chết — `insighthub-do2603-redis-1`
vẫn `running` bình thường suốt quá trình). ARQ worker retry 5 lần theo
backoff rồi raise `ConnectionError`, process exit — container chuyển
`exited`. Nguyên nhân gốc là `REDIS_URL` của service `ingestion-worker`
trỏ sai hostname, không khớp tên service `redis` trong `docker-compose.yml`.

## Fix
Revert `REDIS_URL` về `redis://redis:6379/0` (đúng tên service compose),
`docker compose up -d --force-recreate ingestion-worker`. Verify:
`docker compose ps ingestion-worker` → `Up (healthy)`.

## Ghi chú
Root cause chỉ thấy được qua log thật (`get-logs`), không đoán được nếu
chỉ nhìn status "exited" — đúng lý do MH9 yêu cầu case study thật thay vì
mô tả lý thuyết.
