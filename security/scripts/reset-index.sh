#!/usr/bin/env bash
# LAB ONLY: wipe InsightHub documents/chunks/embedding identity in minikube, then re-upload
# sample-docs. Needed once when embeddings move to the gateway (new embedding identity ->
# index_identity_conflict otherwise). Never run against a real environment.
#   bash security/scripts/reset-index.sh [api_url]
set -euo pipefail
ns=insighthub-local
api=${1:-http://127.0.0.1:18000}
kubectl -n "$ns" exec deploy/insighthub-postgres -- \
  psql -U insighthub -d insighthub -v ON_ERROR_STOP=1 \
  -c "TRUNCATE chunks, documents RESTART IDENTITY CASCADE; DELETE FROM embedding_index;"
root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
for f in "$root"/sample-docs/so-tay-van-hanh.md "$root"/sample-docs/service-level-objectives.md "$root"/sample-docs/huong-dan-nguoi-moi.md; do
  curl -sf -X POST "$api/documents" -F "file=@$f" >/dev/null && echo "uploaded $(basename "$f")"
done
sleep 15
curl -s "$api/documents" | python3 -c "import json,sys;[print(d['filename'],d['status'],d['error_code']) for d in json.load(sys.stdin)]"
