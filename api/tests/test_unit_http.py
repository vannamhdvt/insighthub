import asyncio
import io
import threading
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from support import configured, queue_stub
from app.core.errors import InvalidDocument, ProviderError
from app.core.metrics import http_requests_total
from app.core.upload_limit import UploadLimitMiddleware
from app.main import app
from app.routers.documents import upload_document
from app.services.ingestion import extract_text


class HttpTests(unittest.TestCase):
    def test_whitespace_question_and_unknown_fields_rejected(self):
        client = TestClient(app)
        for data in (
            {"question": "   "},
            {"question": "q", "top_k": 0},
            {"question": "q", "unknown": 1},
        ):
            with self.subTest(data=data):
                self.assertEqual(client.post("/chat", json=data).status_code, 422)

    def test_provider_error_response_is_sanitized(self):
        with patch("app.routers.chat.retrieve", side_effect=ProviderError()):
            response = TestClient(app).post("/chat", json={"question": "question"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["code"], "provider_error")
        self.assertNotIn("Traceback", response.text)

    def test_unexpected_errors_are_sanitized_and_counted_as_500(self):
        with patch(
            "app.routers.chat.retrieve",
            side_effect=RuntimeError("secret-token private content"),
        ):
            response = TestClient(app).post("/chat", json={"question": "question"})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret", response.text)

    def test_empty_upload_returns_422(self):
        with patch("app.routers.documents.get_conn") as connection:
            response = TestClient(app).post(
                "/documents", files={"file": ("empty.txt", b"")}
            )
        self.assertEqual(response.status_code, 422)
        connection.assert_not_called()

    def test_upload_returns_202_and_never_ingests_in_the_request(self):
        from app.core import staging

        self.addCleanup(staging.discard, 42)
        with (
            queue_stub() as queued,
            patch("app.routers.documents._create_pending", return_value=42),
            patch("app.services.ingestion.process_document") as ingest,
        ):
            response = TestClient(app).post(
                "/documents", files={"file": ("async.txt", b"async ingestion")}
            )
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(
            (body["id"], body["status"], body["chunk_count"]), (42, "pending", 0)
        )
        self.assertEqual(len(queued), 1)
        # The whole point of Day 1: the request path must not ingest anything.
        ingest.assert_not_called()

    def test_file_read_is_bounded_and_closed(self):
        class GuardedFile(io.BytesIO):
            def read(self, size=-1):
                self.requested = size
                if size < 0:
                    raise AssertionError("Unbounded read")
                return super().read(size)

        stream = GuardedFile(b"12345")
        # Day 1: the handler is async now, but the read must stay bounded and the
        # file must still be closed before any size decision is made.
        with configured(max_upload_bytes=4), self.assertRaises(HTTPException) as raised:
            asyncio.run(upload_document(UploadFile(filename="test.txt", file=stream)))
        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(stream.requested, 5)
        self.assertTrue(stream.closed)

    def test_invalid_documents_and_blank_pdf_are_422_errors(self):
        writer, output = PdfWriter(), io.BytesIO()
        writer.add_blank_page(width=72, height=72)
        writer.write(output)
        for filename, content in (
            ("a.txt", b""),
            ("a.md", b" \n\t"),
            ("a.txt", b"\xff"),
            ("a.txt", b"contains\x00null"),
            ("a.pdf", b"not a pdf"),
            ("a.pdf", output.getvalue()),
        ):
            with (
                self.subTest(filename=filename, content=content[:10]),
                self.assertRaises(InvalidDocument),
            ):
                extract_text(filename, content)
        self.assertEqual(extract_text("a.txt", b"\xef\xbb\xbfhello"), "hello")

    def test_metrics_route_labels_do_not_include_user_paths_or_methods(self):
        client = TestClient(app)
        for i in range(20):
            client.request(f"UNKNOWN{i}", f"/unknown-{i}")
        with patch("app.routers.documents.get_conn") as get_conn:
            get_conn.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
            for i in range(20):
                client.delete(f"/documents/{1000 + i}")
        labels = [
            sample.labels
            for metric in http_requests_total.collect()
            for sample in metric.samples
        ]
        endpoints = {label["endpoint"] for label in labels}
        self.assertIn("__unmatched__", endpoints)
        self.assertIn("/documents/{document_id}", endpoints)
        self.assertFalse(
            any("unknown-" in value or "/documents/10" in value for value in endpoints)
        )
        self.assertFalse(any(label["method"].startswith("UNKNOWN") for label in labels))


class AsyncHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_remains_responsive_during_blocking_chat(self):
        started, release = threading.Event(), threading.Event()

        def blocked(*args, **kwargs):
            started.set()
            release.wait(timeout=5)
            return []

        with patch("app.routers.chat.retrieve", side_effect=blocked):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                task = asyncio.create_task(
                    client.post("/chat", json={"question": "question"})
                )
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 1))
                    response = await asyncio.wait_for(client.get("/healthz"), timeout=1)
                    self.assertEqual(response.status_code, 200)
                finally:
                    release.set()
                    await task

    async def test_chunked_upload_limit_stops_before_parser_and_without_content_length(
        self,
    ):
        called, sent = [], []
        messages = iter(
            [
                {"type": "http.request", "body": b"a" * 40000, "more_body": True},
                {"type": "http.request", "body": b"b" * 40000, "more_body": True},
            ]
        )

        async def inner(scope, receive, send):
            called.append(True)

        async def receive():
            return next(messages)

        async def send(message):
            sent.append(message)

        with configured(max_upload_bytes=4):
            await UploadLimitMiddleware(inner)(
                {"type": "http", "method": "POST", "path": "/documents", "headers": []},
                receive,
                send,
            )
        self.assertEqual(called, [])
        self.assertEqual(sent[0]["status"], 413)
