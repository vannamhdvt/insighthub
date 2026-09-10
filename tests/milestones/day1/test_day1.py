"""Day 1 milestone contract: async ingestion through Redis/ARQ and a real worker.

These tests run against the live Compose stack (INSIGHTHUB_API_URL) and against
the repository source (INSIGHTHUB_REPO_ROOT). They use only the standard library
plus pytest, because the verifier runs them with plugin autoloading disabled and
a verification-only virtualenv.

Nothing here is skipped or xfailed: a missing stack is a failure, not a skip.
"""

import ast
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

API_URL = os.environ.get("INSIGHTHUB_API_URL", "http://localhost:8000").rstrip("/")
REPO_ROOT = Path(os.environ.get("INSIGHTHUB_REPO_ROOT", ".")).resolve()

# Budgets come from the Day 1 specification, not from local timing luck.
ACCEPT_BUDGET_SECONDS = 1.0
READY_BUDGET_SECONDS = 30.0
ACCEPTED_STATES = {"pending", "queued", "processing", "ready"}
SAMPLE = (
    b"# InsightHub Day 1\n\n"
    b"Upload returns 202 and the ingestion-worker performs chunking and embedding.\n"
    b"Redis and ARQ only carry the document id, never the document content.\n"
)


def request(method, path, data=None, headers=None, timeout=10):
    """Minimal HTTP helper returning (status, parsed body or raw bytes)."""
    url = API_URL + path
    call = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(call, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except urllib.error.URLError as error:
        pytest.fail(f"{method} {path} unreachable: {error.reason}")
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, raw


def upload(filename, content, timeout=10):
    """POST a multipart upload and return (status, body, elapsed_seconds)."""
    boundary = "Day1" + uuid.uuid4().hex
    payload = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: text/markdown\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    headers = {"Content-Type": "multipart/form-data; boundary=" + boundary}
    start = time.monotonic()
    status, body = request("POST", "/documents", payload, headers, timeout)
    return status, body, time.monotonic() - start


def documents():
    status, body = request("GET", "/documents")
    assert status == 200, f"GET /documents returned {status}"
    assert isinstance(body, list), "GET /documents must stay a list"
    return body


def find(document_id):
    matches = [d for d in documents() if str(d.get("id")) == str(document_id)]
    assert len(matches) <= 1, "Duplicate document ids in GET /documents"
    return matches[0] if matches else None


def wait_for_ready(document_id, budget=READY_BUDGET_SECONDS):
    """Poll GET /documents, the only status channel in the starter contract."""
    deadline = time.monotonic() + budget
    last = None
    while time.monotonic() < deadline:
        last = find(document_id)
        assert last is not None, f"Document {document_id} disappeared while polling"
        assert last["status"] in ACCEPTED_STATES, (
            f"Worker left document {document_id} in {last['status']} "
            f"(error_code={last.get('error_code')})"
        )
        if last["status"] == "ready":
            return last
        time.sleep(0.5)
    pytest.fail(f"Document {document_id} not ready within {budget}s: {last}")


def source(relative):
    path = REPO_ROOT / relative
    assert path.is_file(), f"Missing expected source file: {relative}"
    return path.read_text(encoding="utf-8")


def called_functions(code):
    """Names actually invoked in the module, ignoring docstrings and comments."""
    names = set()
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Name):
                names.add(function.id)
            elif isinstance(function, ast.Attribute):
                names.add(function.attr)
    return names


@pytest.fixture(scope="module")
def accepted():
    """One upload shared by the async tests, so the stack is exercised once."""
    filename = f"day1-{uuid.uuid4().hex}.md"
    status, body, elapsed = upload(filename, SAMPLE)
    assert status == 202, f"Upload must return 202 async, got {status}: {body}"
    return {"filename": filename, "body": body, "elapsed": elapsed}


@pytest.fixture(scope="module")
def ingested(accepted):
    return wait_for_ready(accepted["body"]["id"])


def test_refactor_regression(ingested):
    """The refactor really moved ingestion out of the request path, and chat still works.

    Takes the ingested fixture so retrieval has at least one ready document: on a
    freshly started stack chat would otherwise answer 404 for lack of data, which
    says nothing about whether the refactor regressed anything.
    """
    router = source("api/app/routers/documents.py")
    assert "status_code=202" in router, "Upload endpoint must be declared 202"
    assert "status_code=201" not in router, "Synchronous 201 contract still present"
    # Match real call sites only. A plain text search also hits docstrings and
    # comments that merely name the function, which is a false positive.
    called = called_functions(router)
    assert "process_document" not in called, (
        "Router still ingests inline; ingestion belongs to ingestion-worker"
    )
    assert "ingest_document_sync" not in called, "Router still calls the sync wrapper"
    assert "enqueue_ingestion" in called, "Router must enqueue the ingestion job"

    core = source("api/app/services/ingestion.py")
    assert "def process_document(" in core, "The reusable ingestion core was removed"
    assert "FOR UPDATE" in core, "Row lock removed from the ingestion core"

    compose = source("docker-compose.yml")
    assert "ingestion-worker:" in compose, "Compose has no ingestion-worker service"
    assert "redis:" in compose, "Compose has no redis service"

    worker = source("ingestion-worker/worker/worker.py")
    assert "process_document" in worker, "Worker does not reuse the ingestion core"
    assert "ingestion_completed" in worker, "Worker emits no completion event"

    # Behavioural half of the regression check: retrieval and chat are unchanged.
    status, chat = request(
        "POST",
        "/chat",
        json.dumps({"question": "InsightHub có những thành phần chính nào?"}).encode(),
        {"Content-Type": "application/json"},
        timeout=30,
    )
    assert status == 200, f"Chat regressed with HTTP {status}: {chat}"
    assert isinstance(chat.get("answer"), str) and chat["answer"].strip()
    assert isinstance(chat.get("sources"), list) and chat["sources"]
    assert isinstance(chat.get("contexts"), list) and chat["contexts"]


def test_empty_input():
    """An unusable upload is rejected before anything is queued."""
    before = len(documents())
    status, body, _ = upload(f"empty-{uuid.uuid4().hex}.md", b"")
    assert status == 422, f"Empty upload must be 422, got {status}: {body}"
    assert isinstance(body, dict) and body.get("code") == "invalid_document"
    assert len(documents()) == before, "A rejected upload must not create a document"

    status, body, _ = upload(f"binary-{uuid.uuid4().hex}.exe", b"payload")
    assert status == 400, f"Unsupported extension must be 400, got {status}"
    assert len(documents()) == before, "A rejected upload must not create a document"


def test_duplicate_or_invalid():
    """Two identical uploads each ingest cleanly; malformed ones are still refused."""
    filename = f"dup-{uuid.uuid4().hex}.md"
    first_status, first, _ = upload(filename, SAMPLE)
    assert first_status == 202, f"First upload {first_status}: {first}"
    second_status, second, _ = upload(filename, SAMPLE)
    assert second_status == 202, f"Second upload {second_status}: {second}"
    assert str(second["id"]) != str(first["id"]), (
        "Each accepted upload owns its own document row"
    )
    one = wait_for_ready(first["id"])
    two = wait_for_ready(second["id"])
    # Identical content must ingest to identical chunk counts, and neither
    # document may disturb the other while both jobs run through the queue.
    assert one["chunk_count"] == two["chunk_count"] > 0
    assert one["embedding_identity_id"] == two["embedding_identity_id"]
    assert find(first["id"])["chunk_count"] == one["chunk_count"]

    long_name = "x" * 260 + ".md"
    status, body, _ = upload(long_name, SAMPLE)
    assert status == 422, f"Invalid filename must be 422, got {status}: {body}"


def test_async_upload(accepted):
    """202 fast, with a document id and a state the client can poll."""
    body, elapsed = accepted["body"], accepted["elapsed"]
    assert elapsed <= ACCEPT_BUDGET_SECONDS, (
        f"Upload answered in {elapsed:.3f}s; the async budget is "
        f"{ACCEPT_BUDGET_SECONDS}s and a slower answer means work is still inline"
    )
    assert isinstance(body, dict), f"Upload body must be an object: {body}"
    assert str(body.get("id")), "Upload response carries no document id"
    assert body.get("status") in ACCEPTED_STATES, f"Unknown accept state: {body}"
    if body["status"] != "ready":
        assert body.get("chunk_count") == 0, "Accepted upload cannot report chunks yet"
    # The starter contract has no per-document status endpoint; polling is the list.
    status, _ = request("GET", f"/documents/{body['id']}/status")
    assert status == 404, "Day 1 must not add a status endpoint outside the contract"


def test_worker_ingests(accepted, ingested):
    """A separate worker process finished the job and recorded a truthful outcome."""
    assert ingested["status"] == "ready"
    assert ingested["filename"] == accepted["filename"]
    assert isinstance(ingested["chunk_count"], int) and ingested["chunk_count"] > 0
    assert ingested.get("error_code") is None
    assert ingested.get("embedding_identity_id"), (
        "Ready document has no embedding identity"
    )

    status, metrics = request("GET", "/metrics")
    assert status == 200, "Metrics endpoint unavailable"
    text = metrics.decode("utf-8", "replace") if isinstance(metrics, bytes) else ""
    assert "insighthub_documents_total" in text, "Document status metric missing"


def test_retry_idempotent(accepted, ingested):
    """A ready document must survive at-least-once delivery unchanged.

    ARQ can deliver a job more than once. The safety net is process_document():
    a row lock plus atomic chunk replacement, and an early return once the row is
    already ready. This test pins both the observable invariant and the source
    contract the worker relies on instead of reimplementing.
    """
    document_id = accepted["body"]["id"]
    baseline = (ingested["chunk_count"], ingested["embedding_identity_id"])

    # Watch the ready row across several polls: a repeated delivery that added or
    # replaced chunks incorrectly would show up as a changing count or a status
    # moving backwards out of ready.
    for _ in range(4):
        current = find(document_id)
        assert current is not None, "Ready document disappeared"
        assert current["status"] == "ready", (
            f"Document left ready state: {current['status']} "
            f"(error_code={current.get('error_code')})"
        )
        assert (current["chunk_count"], current["embedding_identity_id"]) == baseline, (
            "Chunk count or embedding identity changed after ready: "
            "a duplicate delivery was not idempotent"
        )
        time.sleep(0.5)

    core = source("api/app/services/ingestion.py")
    assert "FOR UPDATE" in core, "Ingestion core lost its row lock"
    assert "DELETE FROM chunks" in core, "Atomic chunk replacement removed"
    worker = source("ingestion-worker/worker/worker.py")
    assert "process_document" in worker, "Worker must delegate to the ingestion core"
    assert "INSERT INTO chunks" not in worker, (
        "Worker writes chunks itself, bypassing the idempotency contract"
    )
