"""Async upload contract: 202 once the job is durably queued, never after ingesting.

Day 1 refactor. The 201 -> 202 change is intentional: the request returns as soon
as the upload is recorded and queued, and the client learns the outcome from
GET /documents (pending -> ready | failed). No status endpoint is added, because
the starter contract does not have one.

Ingestion moved to ingestion-worker; this module must never call
process_document() again, or the refactor is undone.
"""

from fastapi import APIRouter, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core import queue, staging
from app.core.config import get_settings
from app.core.db import get_conn
from app.core.errors import InvalidDocument

router = APIRouter(prefix="/documents", tags=["documents"])
ALLOWED_EXT = (".txt", ".md", ".pdf")


def _create_pending(filename: str, digest: str) -> int:
    return _execute(
        "INSERT INTO documents (filename, status, content_sha256) "
        "VALUES (%s, 'pending', %s) RETURNING id",
        (filename, digest),
    )[0]


def _mark_failed(document_id: int) -> None:
    # A row nothing can pick up must say so instead of sitting in pending forever.
    _execute(
        "UPDATE documents SET status = 'failed', chunk_count = 0, "
        "embedding_identity_id = NULL, error_code = 'internal_error' WHERE id = %s",
        (document_id,),
    )


def _execute(statement: str, parameters: tuple):
    with get_conn() as conn:
        return conn.execute(statement, parameters).fetchone()


@router.post("", status_code=202)
async def upload_document(file: UploadFile):
    settings = get_settings()
    try:
        if not file.filename or not file.filename.lower().endswith(ALLOWED_EXT):
            raise HTTPException(400, "Chỉ chấp nhận: .txt, .md, .pdf")
        if len(file.filename) > 255 or "\x00" in file.filename:
            raise HTTPException(422, "Tên file không hợp lệ.")
        content = await file.read(settings.max_upload_bytes + 1)
    finally:
        await file.close()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(413, "File vượt quá giới hạn upload.")
    if not content:
        raise InvalidDocument()

    digest = staging.digest_of(content)
    # Database and filesystem calls block, so they run off the event loop. Doing
    # them inline would stall the API and break the sub-second 202 budget.
    document_id = await run_in_threadpool(_create_pending, file.filename, digest)
    try:
        await run_in_threadpool(staging.stage, document_id, content)
        await queue.enqueue_ingestion(document_id, file.filename, digest)
    except Exception:
        await run_in_threadpool(_mark_failed, document_id)
        raise
    return {
        "id": document_id,
        "filename": file.filename,
        "status": "pending",
        "chunk_count": 0,
        "mode": settings.rag_mode,
        "embedding_identity_id": settings.embedding_identity_id,
    }


@router.get("")
def list_documents():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, filename, status, chunk_count, created_at, "
            "embedding_identity_id, error_code FROM documents ORDER BY created_at DESC"
        ).fetchall()
    return [
        {
            "id": r[0],
            "filename": r[1],
            "status": r[2],
            "chunk_count": r[3],
            "created_at": r[4].isoformat(),
            "embedding_identity_id": r[5],
            "error_code": r[6],
        }
        for r in rows
    ]


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: int):
    with get_conn() as conn:
        result = conn.execute(
            "DELETE FROM documents WHERE id = %s RETURNING id",
            (document_id,),
        ).fetchone()
    if result is None:
        raise HTTPException(404, "Không tìm thấy tài liệu.")
    # Chunks cascade in SQL; the staged upload is this service's own leftover.
    staging.discard(document_id)
