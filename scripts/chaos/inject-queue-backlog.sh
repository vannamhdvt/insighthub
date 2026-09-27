#!/usr/bin/env bash
# Incident #2 - queue backlog. Stops the ingestion-worker and uploads UPLOADS
# documents; restores the worker after DURATION. Expected: InsightHubQueueBacklogAnomaly.
#   UPLOADS=30 DURATION=10m API_URL=http://localhost:18000 scripts/chaos/inject-queue-backlog.sh
source "$(dirname "$0")/_lib.sh"
UPLOADS=${UPLOADS:-30}
API_URL=${API_URL:-http://localhost:18000}
require_loadgen
injected=$(now)
echo "[$injected] inject: scale $RELEASE-ingestion-worker to 0, upload $UPLOADS documents"
kubectl -n "$NS" scale "deploy/$RELEASE-ingestion-worker" --replicas=0
kubectl -n "$NS" wait --for=delete pod -l app.kubernetes.io/component=ingestion-worker --timeout=2m || true
for i in $(seq 1 "$UPLOADS"); do
  printf '# Backlog drill %s\n\nDocument %s uploaded while the worker is stopped (%s).\n' "$injected" "$i" "$RANDOM-$i" > "/tmp/insighthub-backlog-$i.md"
  curl -s -o /dev/null -w "upload $i: %{http_code}\n" -X POST "$API_URL/documents" -F "file=@/tmp/insighthub-backlog-$i.md"
  rm -f "/tmp/insighthub-backlog-$i.md"
done
echo "holding failure for $DURATION ..."; sleep "$(to_seconds "$DURATION")"
recovered=$(now)
echo "[$recovered] recover: scale worker to 1"
kubectl -n "$NS" scale "deploy/$RELEASE-ingestion-worker" --replicas=1
wait_rollout "$RELEASE-ingestion-worker"
echo "observing drain for 5m ..."; sleep 300
write_window incident-2 queue-backlog "$injected" "$recovered"
