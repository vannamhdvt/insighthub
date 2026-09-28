"""Background worker: lấy job từ durable queue, xử lý với deadline riêng, retry bounded."""
from __future__ import annotations

import asyncio
import logging

from .queue import EventQueue, Job
from .service import ChatOpsService

logger = logging.getLogger("chatops-bot.worker")


async def process_one(queue: EventQueue, service: ChatOpsService, deadline: float, max_attempts: int) -> Job | None:
    job = queue.claim()
    if job is None:
        return None
    try:
        await asyncio.wait_for(service.handle(job.payload), timeout=deadline)
    except Exception as exc:  # noqa: BLE001 - mọi lỗi đều đi qua retry bounded
        status = queue.fail(job, f"{type(exc).__name__}: {exc}", max_attempts)
        logger.warning("job %s failed attempt=%s -> %s (%s)", job.event_id, job.attempts, status, type(exc).__name__)
    else:
        queue.complete(job.event_id)
    return job


async def run_worker(queue: EventQueue, service: ChatOpsService, deadline: float, max_attempts: int,
                     stop: asyncio.Event, idle: float = 0.3) -> None:
    while not stop.is_set():
        job = await process_one(queue, service, deadline, max_attempts)
        if job is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=idle)
            except asyncio.TimeoutError:
                pass
