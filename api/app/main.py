"""InsightHub synchronous starter API."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.db import close_pool, get_conn, initialize_database
from app.core.errors import ServiceError
from app.core.queue import close_pool as close_queue_pool
from app.core.metrics import documents_total, http_requests_total
from app.core.upload_limit import UploadLimitMiddleware
from app.routers import chat, documents, health

settings = get_settings()
logging.basicConfig(level=settings.log_level)
# SDK/transport debugging can expose URLs and headers. Keep it out of lab logs.
for name in ("httpx", "httpcore", "pypdf", "psycopg.pool"):
    logging.getLogger(name).setLevel(logging.CRITICAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await run_in_threadpool(initialize_database)
        yield
    finally:
        # Redis is only a queue client here; a stale pool must not outlive the app.
        await close_queue_pool()
        await run_in_threadpool(close_pool)


app = FastAPI(title=settings.app_name, version="0.2.3", lifespan=lifespan)
app.add_middleware(UploadLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    return JSONResponse(
        {"detail": exc.message, "code": exc.code}, status_code=exc.status_code
    )


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    except Exception:
        # Never expose uncaught driver/provider exception text to clients.
        return JSONResponse(
            {"detail": "Không thể xử lý yêu cầu.", "code": "internal_error"}, 500
        )
    finally:
        route = request.scope.get("route")
        endpoint = getattr(route, "path", "__unmatched__")
        method = (
            request.method
            if request.method
            in {
                "GET",
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
                "HEAD",
                "OPTIONS",
                "TRACE",
                "CONNECT",
            }
            else "OTHER"
        )
        http_requests_total.labels(method, endpoint, str(status)).inc()


@app.get("/metrics")
def metrics():
    with get_conn() as conn:
        counts = dict(
            conn.execute(
                "SELECT status, count(*) FROM documents GROUP BY status"
            ).fetchall()
        )
    for status in ("pending", "ready", "failed"):
        documents_total.labels(status).set(counts.get(status, 0))
    return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


app.include_router(health.router)
app.include_router(documents.router)
app.include_router(chat.router)


@app.get("/")
def root():
    return {
        "service": settings.app_name,
        "version": "0.2.3",
        "docs": "/docs",
        "mode": settings.rag_mode,
    }
