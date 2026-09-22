"""InsightHub ingestion worker (Day 1).

This is the only component that runs ingestion. It consumes ARQ jobs carrying a
document id, filename and content digest, reads the staged bytes from the shared
upload volume and calls the unchanged process_document() core.

Two behaviours matter for review:

* Idempotency - a duplicate or repeated delivery is safe because
  process_document() holds a row lock and replaces chunks atomically. The worker
  deliberately never touches the chunks table itself.
* Structured logs - one JSON object per line on stdout, including an
  ingestion_completed event carrying document_id, status and an RFC3339 timestamp
  so a run can be correlated with the upload that caused it.

A failing job is recorded and returned rather than raised: process_document()
has already committed a truthful status and error_code, so re-raising would only
make ARQ repeat the same outcome.
"""

import asyncio
import datetime as dt
import json
import sys

from arq.connections import RedisSettings

from app.core import staging
from app.core.config import get_settings
from app.services.ingestion import process_document

TASK_NAME = "ingest_document"


def now_rfc3339() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def log(event: str, **fields) -> None:
    """Emit one structured line on stdout.

    Only identifiers, statuses and error codes are logged. Document content,
    provider payloads and the Redis DSN never appear here.
    """
    record = {"event": event, "service": "ingestion-worker", "timestamp": now_rfc3339()}
    record.update(fields)
    sys.stdout.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


async def ingest_document(ctx, document_id: int, filename: str, digest: str) -> dict:
    """Process one queued document. Returns a small, loggable result."""
    log("ingestion_started", document_id=document_id, filename=filename)
    try:
        # Blocking file and database work stays off the event loop, otherwise one
        # slow document stalls every other job in this worker.
        content = await asyncio.to_thread(staging.load, document_id, digest)
        chunk_count = await asyncio.to_thread(
            process_document, document_id, filename, content
        )
    except Exception as exc:
        code = getattr(exc, "code", "internal_error")
        log(
            "ingestion_completed",
            document_id=document_id,
            filename=filename,
            status="failed",
            error_code=code,
        )
        return {"document_id": document_id, "status": "failed", "error_code": code}

    await asyncio.to_thread(staging.discard, document_id)
    log(
        "ingestion_completed",
        document_id=document_id,
        filename=filename,
        status="ready",
        chunk_count=chunk_count,
    )
    return {"document_id": document_id, "status": "ready", "chunk_count": chunk_count}


# ARQ registers the coroutine under its own __name__, so ingest_document must keep
# matching TASK_NAME and app.core.queue.INGEST_TASK. Renaming it silently orphans
# every queued job.


async def startup(ctx) -> None:
    settings = get_settings()
    log("worker_started", mode=settings.rag_mode, upload_dir=settings.upload_dir)


async def shutdown(ctx) -> None:
    log("worker_stopped")


class WorkerSettings:
    """ARQ entrypoint: `arq worker.worker.WorkerSettings`."""

    functions = [ingest_document]
    on_startup = startup
    on_shutdown = shutdown
    # A single document must not be able to hold a slot forever.
    job_timeout = 300
    # Written to Redis so `arq --check` can be used as the container healthcheck.
    health_check_interval = 15
    # Same REDIS_URL as the API: a mismatch here is the classic Day 1 failure where
    # uploads return 202 and nothing is ever processed.
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
