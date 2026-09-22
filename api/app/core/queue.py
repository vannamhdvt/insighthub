"""Day 1 queue boundary: the API enqueues, it never ingests.

The job carries only the document id, filename and content digest. Upload bytes
stay on the shared volume (app/core/staging.py), so Redis remains a coordination
channel and never holds document content.

ARQ delivers at least once. Safety under a duplicate or retried delivery comes
from process_document(): it holds a row lock and replaces chunks atomically. That
contract, not this module, is what prevents duplicate chunks.
"""

import asyncio

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings

INGEST_TASK = "ingest_document"

_pool: ArqRedis | None = None
_lock = asyncio.Lock()


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        async with _lock:
            if _pool is None:
                _pool = await create_pool(
                    RedisSettings.from_dsn(get_settings().redis_url)
                )
    return _pool


async def close_pool() -> None:
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        await pool.aclose()


async def enqueue_ingestion(document_id: int, filename: str, digest: str) -> None:
    pool = await get_pool()
    await pool.enqueue_job(INGEST_TASK, int(document_id), filename, digest)
