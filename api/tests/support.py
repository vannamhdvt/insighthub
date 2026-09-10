"""Test configuration never uses developer credentials or sends paid requests."""

import os
import tempfile
from contextlib import contextmanager
from unittest.mock import patch

# Day 1: staging and queue settings must never point at the real lab volume or a
# real Redis database during tests.
TEST_UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "insighthub-test-uploads")
TEST_REDIS_URL = "redis://127.0.0.1:6379/15"

os.environ.update(
    RAG_MODE="fixture",
    LLM_PROVIDER="fixture",
    EMBEDDING_PROVIDER="fixture",
    LLM_MODEL="",
    EMBEDDING_MODEL="",
    EMBEDDING_DIM="1024",
    UPLOAD_DIR=TEST_UPLOAD_DIR,
    REDIS_URL=TEST_REDIS_URL,
)
# Imported after the environment is prepared so the settings cache never sees
# developer credentials or the real upload volume.
from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()


@contextmanager
def configured(**values):
    defaults = {
        "RAG_MODE": "fixture",
        "LLM_PROVIDER": "fixture",
        "EMBEDDING_PROVIDER": "fixture",
        "LLM_MODEL": "",
        "EMBEDDING_MODEL": "",
        "EMBEDDING_DIM": "1024",
        "GEMINI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "VOYAGE_API_KEY": "",
        "OPENAI_API_KEY": "",
        "GEMINI_CHAT_MODEL": "",
        "ANTHROPIC_CHAT_MODEL": "",
        "OLLAMA_CHAT_MODEL": "",
        "OPENAI_CHAT_MODEL": "",
        "OPENAI_BASE_URL": "",
        "EMBEDDING_REVISION": "1",
        "CHUNK_SIZE": "800",
        "CHUNK_OVERLAP": "100",
        "UPLOAD_DIR": TEST_UPLOAD_DIR,
        "REDIS_URL": TEST_REDIS_URL,
    }
    defaults.update({key.upper(): str(value) for key, value in values.items()})
    with patch.dict(os.environ, defaults):
        get_settings.cache_clear()
        try:
            yield get_settings()
        finally:
            get_settings.cache_clear()


@contextmanager
def queue_stub():
    """Uploads must reach 202 without a live Redis.

    The real queue hop is covered end to end by tests/milestones/day1 against the
    running Compose stack; here we only assert the API enqueues instead of ingesting.
    """
    calls = []

    async def enqueue(document_id, filename, digest):
        calls.append((document_id, filename, digest))
        return True

    with patch("app.core.queue.enqueue_ingestion", side_effect=enqueue):
        yield calls


def run_worker_once(document_id, filename):
    """Run exactly what the worker runs: process_document over the staged bytes."""
    from app.core import staging
    from app.services.ingestion import process_document

    return process_document(document_id, filename, staging.load(document_id))


def real_config(provider="openai", **extra):
    values = {
        "rag_mode": "real",
        "llm_provider": provider,
        "embedding_provider": "openai",
        "openai_base_url": "https://gateway.example/v1",
        "openai_api_key": "test-secret",
        "llm_model": "test-chat-model",
    }
    values.update(extra)
    return configured(**values)
