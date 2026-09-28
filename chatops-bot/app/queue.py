"""Durable event queue (SQLite) giữa HTTP ACK và worker trả lời.

- enqueue() chạy trong request, dedup theo Slack event_id (PRIMARY KEY): Slack retry
  cùng event_id (X-Slack-Retry-Num) chỉ được xử lý 1 lần.
- Worker claim() theo lease; process chết giữa chừng thì job quay về pending khi lease hết.
- Retry bounded: tối đa max_attempts, backoff tăng dần, quá giới hạn thì `dead`.
SQLite đủ cho 1 replica local; chạy nhiều replica thì thay bằng Redis/SQS cùng contract.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id   TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    attempts   INTEGER NOT NULL DEFAULT 0,
    next_at    REAL NOT NULL,
    lease_until REAL,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ready ON events(status, next_at);
"""


@dataclass
class Job:
    event_id: str
    payload: dict[str, Any]
    attempts: int


class EventQueue:
    def __init__(self, path: Path, lease_seconds: float = 120.0) -> None:
        self.path = path
        self.lease_seconds = lease_seconds
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def enqueue(self, event_id: str, payload: dict[str, Any]) -> bool:
        """True nếu là event mới; False nếu trùng (đã nhận trước đó)."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO events(event_id, payload, next_at, created_at, updated_at) VALUES (?,?,?,?,?)",
                (event_id, json.dumps(payload), now, now, now),
            )
            return cur.rowcount == 1

    def claim(self) -> Job | None:
        now = time.time()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # Lease hết hạn = worker trước đã chết khi đang xử lý -> cho chạy lại.
                self._conn.execute(
                    "UPDATE events SET status='pending' WHERE status='processing' AND lease_until < ?", (now,)
                )
                row = self._conn.execute(
                    "SELECT event_id, payload, attempts FROM events WHERE status='pending' AND next_at <= ? "
                    "ORDER BY created_at LIMIT 1",
                    (now,),
                ).fetchone()
                if row is None:
                    self._conn.execute("COMMIT")
                    return None
                self._conn.execute(
                    "UPDATE events SET status='processing', attempts=attempts+1, lease_until=?, updated_at=? "
                    "WHERE event_id=?",
                    (now + self.lease_seconds, now, row[0]),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return Job(event_id=row[0], payload=json.loads(row[1]), attempts=row[2] + 1)

    def complete(self, event_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE events SET status='done', lease_until=NULL, updated_at=? WHERE event_id=?",
                (time.time(), event_id),
            )

    def fail(self, job: Job, error: str, max_attempts: int) -> str:
        """Trả về status mới: 'pending' (sẽ retry) hoặc 'dead'."""
        now = time.time()
        status = "dead" if job.attempts >= max_attempts else "pending"
        backoff = min(2 ** job.attempts, 30)
        with self._lock:
            self._conn.execute(
                "UPDATE events SET status=?, next_at=?, lease_until=NULL, last_error=?, updated_at=? WHERE event_id=?",
                (status, now + backoff, error[:300], now, job.event_id),
            )
        return status

    def status(self, event_id: str) -> tuple[str, int] | None:
        with self._lock:
            row = self._conn.execute("SELECT status, attempts FROM events WHERE event_id=?", (event_id,)).fetchone()
        return (row[0], row[1]) if row else None

    def depth(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM events WHERE status IN ('pending','processing')").fetchone()[0]
