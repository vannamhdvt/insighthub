#!/usr/bin/env bash
# Incident #3 - error burst. CHAOS_LLM_ERROR_RATE makes a share of /chat calls fail
# with 502 provider_error for DURATION. Expected: InsightHubErrorRateAnomaly (critical).
#   RATE=0.6 DURATION=10m scripts/chaos/inject-error-burst.sh
source "$(dirname "$0")/_lib.sh"
RATE=${RATE:-0.6}
require_loadgen
injected=$(now)
echo "[$injected] inject: CHAOS_LLM_ERROR_RATE=$RATE on $RELEASE-api"
kubectl -n "$NS" set env "deploy/$RELEASE-api" CHAOS_LLM_ERROR_RATE="$RATE"
wait_rollout "$RELEASE-api"
echo "holding failure for $DURATION ..."; sleep "$(to_seconds "$DURATION")"
recovered=$(now)
echo "[$recovered] recover: remove CHAOS_LLM_ERROR_RATE"
kubectl -n "$NS" set env "deploy/$RELEASE-api" CHAOS_LLM_ERROR_RATE-
wait_rollout "$RELEASE-api"
echo "observing recovery for 5m ..."; sleep 300
write_window incident-3 error-burst "$injected" "$recovered"
