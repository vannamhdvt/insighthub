"""Reusable ingestion core, now called only by ingestion-worker.

Day 1 removed the ingest_document_sync() wrapper: nothing in the request path may
run this module any more. process_document() is unchanged on purpose - its row
lock plus atomic chunk replacement is what makes at-least-once queue delivery
safe, so the retry policy in the worker leans on it instead of reimplementing it.
"""

import hashlib
import io
import json
import logging

from app.core.config import get_settings
from app.core.db import get_conn
from app.core.errors import (
    DocumentConflict,
    DocumentNotFound,
    InvalidDocument,
    ServiceError,
)
from app.core.index import check_schema, ensure_index_identity
from app.core.metrics import ingestion_errors_total
from app.services.chunking import chunk_text
from app.services.embeddings import embed, validate_vectors

logger = logging.getLogger("insighthub.ingestion")
MAX_EXTRACTED_CHARS = 2_000_000
MAX_PDF_PAGES = 500


def extract_text(filename: str, content: bytes) -> str:
    try:
        if not content:
            raise InvalidDocument()
        if filename.lower().endswith((".txt", ".md")):
            text = content.decode("utf-8-sig")
        elif filename.lower().endswith(".pdf"):
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted or len(reader.pages) > MAX_PDF_PAGES:
                raise InvalidDocument()
            pieces, size = [], 0
            for page in reader.pages:
                piece = page.extract_text() or ""
                size += len(piece)
                if size > MAX_EXTRACTED_CHARS:
                    raise InvalidDocument()
                pieces.append(piece)
            text = "\n".join(pieces)
        else:
            raise InvalidDocument()
        if not text.strip() or "\x00" in text or len(text) > MAX_EXTRACTED_CHARS:
            raise InvalidDocument()
        return text
    except InvalidDocument:
        raise
    except Exception:
        raise InvalidDocument() from None


def _pipeline_id() -> str:
    settings = get_settings()
    return hashlib.sha256(
        json.dumps(
            {
                "version": "extract-chunk-v1",
                "chunk_size": settings.chunk_size,
                "chunk_overlap": settings.chunk_overlap,
                "embedding_identity": settings.embedding_identity_id,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def process_document(document_id: int, filename: str, content: bytes) -> int:
    """Same ID + bytes + pipeline is a no-op after success, including concurrent retries.

    A row lock spans the synchronous provider calls. A savepoint atomically replaces
    chunks and metadata. Failures commit truthful status while still holding the lock,
    so a delayed failure cannot overwrite a later successful retry.
    """
    settings = get_settings()
    digest, pipeline_id = hashlib.sha256(content).hexdigest(), _pipeline_id()
    failure = None
    chunk_count = 0
    with get_conn() as conn:
        with conn.transaction():
            check_schema(conn)
            row = conn.execute(
                "SELECT filename, status, chunk_count, content_sha256, pipeline_id "
                "FROM documents WHERE id = %s FOR UPDATE",
                (document_id,),
            ).fetchone()
            if row is None:
                raise DocumentNotFound()
            if (
                row[0] != filename
                or row[3] not in (None, digest)
                or row[4] not in (None, pipeline_id)
            ):
                raise DocumentConflict()
            if row[1] == "ready":
                ensure_index_identity(conn, claim=False)
                return row[2]
            conn.execute(
                "UPDATE documents SET content_sha256 = %s, pipeline_id = %s WHERE id = %s",
                (digest, pipeline_id, document_id),
            )
            try:
                with (
                    conn.transaction()
                ):  # Savepoint retains the outer document row lock.
                    ensure_index_identity(conn, claim=False)
                    if len(content) > settings.max_upload_bytes:
                        raise InvalidDocument()
                    chunks = chunk_text(extract_text(filename, content))
                    ensure_index_identity(conn, claim=True)
                    vectors = validate_vectors(
                        embed(chunks, input_type="document"),
                        len(chunks),
                        settings.embedding_dim,
                    )
                    conn.execute(
                        "DELETE FROM chunks WHERE document_id = %s", (document_id,)
                    )
                    conn.execute(
                        "UPDATE documents SET embedding_identity_id = %s WHERE id = %s",
                        (settings.embedding_identity_id, document_id),
                    )
                    for index, (chunk, vector) in enumerate(
                        zip(chunks, vectors, strict=True)
                    ):
                        conn.execute(
                            "INSERT INTO chunks "
                            "(document_id, chunk_index, chunk_text, embedding, embedding_identity_id) "
                            "VALUES (%s, %s, %s, %s::vector, %s)",
                            (
                                document_id,
                                index,
                                chunk,
                                vector,
                                settings.embedding_identity_id,
                            ),
                        )
                    chunk_count = len(chunks)
                    conn.execute(
                        "UPDATE documents SET status = 'ready', chunk_count = %s, error_code = NULL "
                        "WHERE id = %s",
                        (chunk_count, document_id),
                    )
            except Exception as exc:
                failure = exc if isinstance(exc, ServiceError) else ServiceError()
                conn.execute(
                    "DELETE FROM chunks WHERE document_id = %s", (document_id,)
                )
                conn.execute(
                    "UPDATE documents SET status = 'failed', chunk_count = 0, "
                    "embedding_identity_id = NULL, error_code = %s WHERE id = %s",
                    (failure.code, document_id),
                )
    if failure is not None:
        ingestion_errors_total.inc()
        logger.warning(
            "Document processing failed: id=%s code=%s", document_id, failure.code
        )
        raise failure from None
    return chunk_count
