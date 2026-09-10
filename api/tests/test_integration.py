"""Opt in with RUN_DB_TESTS=1 and mount init.sql as TEST_SCHEMA_PATH in Docker.

Each test run owns a random PostgreSQL schema. Only that schema is truncated/dropped.
No paid providers are called. A missing DB/schema fixture fails an opted-in run.
"""

import concurrent.futures
import os
from pathlib import Path
import threading
import unittest
import uuid
from unittest.mock import patch

from support import configured, queue_stub, real_config, run_worker_once
import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool
from fastapi.testclient import TestClient

from app.core import db
from app.core.config import get_settings
from app.core.errors import (
    DocumentConflict,
    IndexIdentityConflict,
    InvalidDocument,
    ProviderError,
    SchemaMismatch,
)
from app.core.index import check_schema
from app.main import app
from app.services.embeddings import _local_embed
from app.services.ingestion import process_document
from app.services.retrieval import retrieve


@unittest.skipUnless(
    os.environ.get("RUN_DB_TESTS") == "1",
    "Set RUN_DB_TESTS=1 for isolated PostgreSQL integration tests",
)
class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ.get("TEST_DATABASE_URL") or get_settings().database_url
        cls.schema = "test_insighthub_" + uuid.uuid4().hex
        source = Path(
            os.environ.get(
                "TEST_SCHEMA_PATH",
                str(Path(__file__).resolve().parents[2] / "infra/db/init.sql"),
            )
        )
        cls.schema_sql = source.read_text()
        cls.old_pool = db._pool
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            # Extension must be installed by the DB bootstrap, never by the test run.
            if not conn.execute(
                "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
            ).fetchone():
                raise RuntimeError(
                    "Initialize pgvector using infra/db/init.sql before integration tests"
                )
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
        cls.addClassCleanup(cls.cleanup_schema)
        with psycopg.connect(
            cls.dsn, options=f"-csearch_path={cls.schema},public"
        ) as conn:
            conn.execute(cls.schema_sql)
        db._pool = ConnectionPool(
            conninfo=cls.dsn,
            min_size=2,
            max_size=10,
            configure=db._configure,
            kwargs={"options": f"-csearch_path={cls.schema},public"},
            open=True,
        )
        db._pool.wait(timeout=15)

    @classmethod
    def cleanup_schema(cls):
        if db._pool is not cls.old_pool:
            db._pool.close()
        db._pool = cls.old_pool
        with psycopg.connect(cls.dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema))
            )

    def setUp(self):
        self.config = configured()
        self.config.__enter__()
        self.addCleanup(self.config.__exit__, None, None, None)
        with db.get_conn() as conn:
            conn.execute(
                "TRUNCATE chunks, documents, embedding_index RESTART IDENTITY CASCADE"
            )
        # Day 1: uploads only enqueue. These tests own the worker step explicitly so
        # each assertion states whether it is about accepting or about ingesting.
        self.queue = queue_stub()
        self.queued = self.queue.__enter__()
        self.addCleanup(self.queue.__exit__, None, None, None)
        self.client = TestClient(app)

    def accept(self, filename, content, expected_status=202):
        """Upload and assert the async accept contract, without ingesting."""
        response = self.client.post("/documents", files={"file": (filename, content)})
        self.assertEqual(response.status_code, expected_status, response.text)
        return response

    def work(self, document_id, filename):
        """Do what ingestion-worker does for one queued job."""
        return run_worker_once(document_id, filename)

    def accept_and_work(self, filename, content):
        document = self.accept(filename, content).json()
        self.assertEqual((document["status"], document["chunk_count"]), ("pending", 0))
        chunk_count = self.work(document["id"], filename)
        return document, chunk_count

    def create_document(self, filename="test.txt"):
        with db.get_conn() as conn:
            return conn.execute(
                "INSERT INTO documents(filename) VALUES (%s) RETURNING id",
                (filename,),
            ).fetchone()[0]

    def state(self, document_id):
        with db.get_conn() as conn:
            return conn.execute(
                "SELECT status, chunk_count, content_sha256, error_code, "
                "(SELECT count(*) FROM chunks WHERE document_id = documents.id) "
                "FROM documents WHERE id = %s",
                (document_id,),
            ).fetchone()

    def test_fixture_upload_retrieve_chat_metrics_delete_end_to_end(self):
        self.assertEqual(self.client.get("/readyz").status_code, 200)
        self.assertEqual(
            self.client.post("/chat", json={"question": "RAG?"}).status_code, 404
        )
        response = self.accept("rag.txt", b"RAG uses retrieved documents.")
        document = response.json()
        self.assertEqual(document["mode"], "fixture")
        # 202 means accepted, not ingested: no chunks exist yet.
        self.assertEqual((document["status"], document["chunk_count"]), ("pending", 0))
        self.assertEqual(self.client.get("/documents").json()[0]["status"], "pending")
        self.assertEqual(len(self.queued), 1)
        self.assertEqual(self.work(document["id"], "rag.txt"), 1)
        after = self.client.get("/documents").json()[0]
        self.assertEqual((after["status"], after["chunk_count"]), ("ready", 1))
        chat = self.client.post(
            "/chat", json={"question": "RAG uses retrieved documents."}
        )
        self.assertEqual(chat.status_code, 200, chat.text)
        self.assertIn("FIXTURE", chat.json()["answer"])
        self.assertEqual(chat.json()["sources"], ["rag.txt"])
        self.assertAlmostEqual(chat.json()["contexts"][0]["similarity"], 1, places=3)
        self.assertEqual(chat.json()["usage"]["source"], "unavailable")
        metrics = self.client.get("/metrics")
        self.assertEqual(metrics.status_code, 200)
        self.assertIn('insighthub_documents_total{status="ready"} 1.0', metrics.text)
        self.assertEqual(
            self.client.delete(f"/documents/{document['id']}").status_code, 204
        )
        with db.get_conn() as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM chunks").fetchone()[0], 0
            )
        self.assertIn(
            'insighthub_documents_total{status="ready"} 0.0',
            self.client.get("/metrics").text,
        )

    def test_successful_retry_is_noop_and_conflicting_payload_is_409(self):
        document_id = self.create_document()
        first = process_document(document_id, "test.txt", b"hello world")
        with patch("app.services.ingestion.embed") as provider:
            self.assertEqual(
                process_document(document_id, "test.txt", b"hello world"), first
            )
            provider.assert_not_called()
        for filename, content in (
            ("test.txt", b"changed"),
            ("other.txt", b"hello world"),
        ):
            with self.assertRaises(DocumentConflict):
                process_document(document_id, filename, content)
        state = self.state(document_id)
        self.assertEqual((state[0], state[1], state[4]), ("ready", first, first))
        self.assertIsNotNone(state[2])

    def test_concurrent_successful_retries_call_provider_once(self):
        document_id = self.create_document()
        started, release = threading.Event(), threading.Event()

        def slow_embed(texts, input_type):
            started.set()
            self.assertTrue(release.wait(timeout=5))
            return _local_embed(texts, 1024)

        with patch("app.services.ingestion.embed", side_effect=slow_embed) as provider:
            with concurrent.futures.ThreadPoolExecutor(2) as executor:
                first = executor.submit(
                    process_document, document_id, "test.txt", b"same"
                )
                self.assertTrue(started.wait(timeout=3))
                second = executor.submit(
                    process_document, document_id, "test.txt", b"same"
                )
                release.set()
                self.assertEqual(first.result(timeout=5), second.result(timeout=5))
            self.assertEqual(provider.call_count, 1)
        self.assertEqual(self.state(document_id)[4], 1)

    def test_failed_attempt_and_concurrent_retry_end_ready(self):
        document_id = self.create_document()
        started, release = threading.Event(), threading.Event()
        calls = []

        def fail_once(texts, input_type):
            calls.append(True)
            if len(calls) == 1:
                started.set()
                release.wait(timeout=5)
                raise ProviderError()
            return _local_embed(texts, 1024)

        with patch("app.services.ingestion.embed", side_effect=fail_once):
            with concurrent.futures.ThreadPoolExecutor(2) as executor:
                first = executor.submit(
                    process_document, document_id, "test.txt", b"same"
                )
                self.assertTrue(started.wait(timeout=3))
                second = executor.submit(
                    process_document, document_id, "test.txt", b"same"
                )
                release.set()
                with self.assertRaises(ProviderError):
                    first.result(timeout=5)
                self.assertEqual(second.result(timeout=5), 1)
        self.assertEqual(self.state(document_id)[0], "ready")
        self.assertEqual(self.state(document_id)[4], 1)
        self.assertIsNone(self.state(document_id)[3])

    def test_failed_vectors_are_atomic_and_can_retry(self):
        document_id = self.create_document()
        for invalid in ([], [[float("nan")] * 1024], [[1.0] * 1023]):
            with (
                patch("app.services.ingestion.embed", return_value=invalid),
                self.assertRaises(ProviderError),
            ):
                process_document(document_id, "test.txt", b"content")
            state = self.state(document_id)
            self.assertEqual((state[0], state[1], state[4]), ("failed", 0, 0))
            self.assertEqual(state[3], "provider_error")
            with db.get_conn() as conn:
                self.assertEqual(
                    conn.execute("SELECT count(*) FROM embedding_index").fetchone()[0],
                    0,
                )
        self.assertEqual(process_document(document_id, "test.txt", b"content"), 1)

    def test_mid_insert_database_failure_rolls_back_all_chunks(self):
        document_id = self.create_document()
        with db.get_conn() as conn:
            conn.execute(
                "CREATE FUNCTION reject_second() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN IF NEW.chunk_index = 1 THEN RAISE EXCEPTION 'secret'; END IF; "
                "RETURN NEW; END $$"
            )
            conn.execute(
                "CREATE TRIGGER reject_second BEFORE INSERT ON chunks "
                "FOR EACH ROW EXECUTE FUNCTION reject_second()"
            )
        try:
            with configured(chunk_size=4, chunk_overlap=0):
                with self.assertRaises(Exception) as raised:
                    process_document(document_id, "test.txt", b"a b c d e f")
                self.assertNotIn("secret", str(raised.exception))
        finally:
            with db.get_conn() as conn:
                conn.execute("DROP TRIGGER reject_second ON chunks")
                conn.execute("DROP FUNCTION reject_second()")
        state = self.state(document_id)
        self.assertEqual((state[0], state[1], state[4]), ("failed", 0, 0))

    def test_empty_extracted_text_is_accepted_then_failed_by_the_worker(self):
        # Bytes are present, so the API cannot know the document is unusable. The
        # 422 contract moved to the worker, which must record a truthful failure.
        document = self.accept("empty.txt", b" \n ").json()
        self.assertEqual(document["status"], "pending")
        with self.assertRaises(InvalidDocument):
            self.work(document["id"], "empty.txt")
        listed = self.client.get("/documents").json()[0]
        self.assertEqual(listed["status"], "failed")
        self.assertEqual(listed["chunk_count"], 0)
        self.assertEqual(listed["error_code"], "invalid_document")

    def test_index_identity_change_rejects_query_upload_and_readiness(self):
        self.accept_and_work("test.txt", b"content")
        with (
            configured(embedding_revision="2"),
            patch("app.services.retrieval.embed") as provider,
        ):
            with self.assertRaises(IndexIdentityConflict):
                retrieve("question")
            provider.assert_not_called()
            # Accepting is still allowed; the identity conflict surfaces where the
            # embedding actually happens, which is now the worker.
            document = self.accept("new.txt", b"new content").json()
            with self.assertRaises(IndexIdentityConflict):
                self.work(document["id"], "new.txt")
            self.assertEqual(
                self.client.get("/documents").json()[0]["error_code"],
                "index_identity_conflict",
            )
            self.assertEqual(self.client.get("/readyz").status_code, 503)
        with db.get_conn() as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM chunks").fetchone()[0], 1
            )

    def test_same_dimension_real_provider_cannot_query_fixture_index(self):
        self.accept_and_work("test.txt", b"content")
        with real_config(), patch("app.services.retrieval.embed") as provider:
            with self.assertRaises(IndexIdentityConflict):
                retrieve("question")
        provider.assert_not_called()

    def test_dimension_mismatch_is_rejected_before_provider(self):
        with configured(embedding_dim=768), db.get_conn() as conn:
            with self.assertRaises(SchemaMismatch):
                check_schema(conn)

    def test_real_gateway_full_pipeline_with_mock_http(self):
        def embedding_response(*args, **kwargs):
            return {
                "data": [
                    {"index": i, "embedding": [1.0] * 1024}
                    for i in range(len(kwargs["payload"]["input"]))
                ],
                "usage": {"prompt_tokens": 5},
            }

        with (
            real_config(),
            patch(
                "app.services.embeddings.post_json",
                side_effect=embedding_response,
            ),
            patch(
                "app.services.llm.post_json",
                return_value={
                    "choices": [
                        {"message": {"content": "Supported answer [nguồn: real.txt]"}}
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8},
                },
            ),
        ):
            document = self.accept("real.txt", b"content").json()
            self.assertEqual(self.work(document["id"], "real.txt"), 1)
            chat = self.client.post("/chat", json={"question": "question"})
            self.assertEqual(chat.status_code, 200, chat.text)
            self.assertEqual(chat.json()["mode"], "real")
            self.assertEqual(chat.json()["usage"]["input_tokens"], 12)
            self.assertEqual(chat.json()["usage"]["source"], "provider")

    def test_provider_failure_is_recorded_by_the_worker_and_metadata_truthful(self):
        with (
            real_config(),
            patch("app.services.embeddings.post_json", side_effect=ProviderError()),
        ):
            # The client already holds a 202; a provider outage must not be able to
            # rewrite that response, only the document's recorded outcome.
            accepted = self.accept("real.txt", b"content").json()
            with self.assertRaises(ProviderError):
                self.work(accepted["id"], "real.txt")
        document = self.client.get("/documents").json()[0]
        self.assertEqual(
            (document["status"], document["chunk_count"], document["error_code"]),
            ("failed", 0, "provider_error"),
        )
