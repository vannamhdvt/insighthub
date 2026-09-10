"""Day 1 content store shared by API and ingestion-worker.

Upload bytes are staged on a volume that both services mount, and the queue only
carries the document id plus its digest. Redis stays a coordination channel: it
never holds document content, so a large upload cannot blow up the queue and job
payloads stay safe to log.

Both services import this module, so the path layout has exactly one definition.
"""

import hashlib
import os
import tempfile
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import InvalidDocument

STAGED_SUFFIX = ".bin"


def digest_of(content: bytes) -> str:
    """Content digest used for queue deduplication and staging integrity."""
    return hashlib.sha256(content).hexdigest()


def root() -> Path:
    path = Path(get_settings().upload_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def staged_path(document_id: int) -> Path:
    # Only the row id names the file: no user-controlled path segment is used.
    return root() / f"{int(document_id)}{STAGED_SUFFIX}"


def stage(document_id: int, content: bytes) -> str:
    """Write staged bytes atomically and return their digest.

    A temporary file in the same directory plus os.replace means the worker can
    never observe a half-written upload, even if the API is killed mid-write.
    """
    if not content:
        raise InvalidDocument()
    target = staged_path(document_id)
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), suffix=".part")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return digest_of(content)


def load(document_id: int, expected_digest: str | None = None) -> bytes:
    """Read staged bytes back, refusing content that does not match the row."""
    try:
        content = staged_path(document_id).read_bytes()
    except OSError:
        raise InvalidDocument() from None
    if not content:
        raise InvalidDocument()
    if expected_digest is not None and digest_of(content) != expected_digest:
        # A digest mismatch means a stale or truncated stage, never a valid retry.
        raise InvalidDocument()
    return content


def discard(document_id: int) -> None:
    """Best-effort cleanup; ingestion state lives in Postgres, not on this volume."""
    staged_path(document_id).unlink(missing_ok=True)
