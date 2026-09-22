# InsightHub ingestion worker - Day 1 deliverable.
# Build context is the repository root so the worker can reuse api/app without
# vendoring a second copy of the ingestion core:
#   build: {context: ., dockerfile: ingestion-worker/Dockerfile}
FROM python:3.12.14-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

FROM base AS deps
COPY ingestion-worker/requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

FROM base AS runtime
RUN groupadd --system --gid 1001 appuser \
    && useradd --system --uid 1001 --gid appuser appuser
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
# The reusable ingestion core stays a single source of truth, owned by the API.
COPY --chown=appuser:appuser api/app ./app
COPY --chown=appuser:appuser ingestion-worker/worker ./worker
# Staged uploads arrive on a volume shared with the API.
RUN mkdir -p /var/lib/insighthub/uploads \
    && chown -R appuser:appuser /var/lib/insighthub
USER appuser
# arq --check reads the health key this worker writes to Redis.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD arq --check worker.worker.WorkerSettings || exit 1
CMD ["arq", "worker.worker.WorkerSettings"]
