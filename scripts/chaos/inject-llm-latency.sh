#!/usr/bin/env bash
# Incident #1 - LLM latency spike. Adds CHAOS_LLM_DELAY_SECONDS to every generation
# for DURATION, then removes it. Expected: InsightHubLLMLatencyAnomaly fires < 5 min.
#   DELAY=3 DURATION=10m scripts/chaos/inject-llm-latency.sh
source "$(dirname "$0")/_lib.sh"
DELAY=${DELAY:-3}
require_loadgen
injected=$(now)
echo "[$injected] inject: CHAOS_LLM_DELAY_SECONDS=$DELAY on $RELEASE-api"
kubectl -n "$NS" set env "deploy/$RELEASE-api" CHAOS_LLM_DELAY_SECONDS="$DELAY"
wait_rollout "$RELEASE-api"
echo "holding failure for $DURATION ..."; sleep "$(to_seconds "$DURATION")"
recovered=$(now)
echo "[$recovered] recover: remove CHAOS_LLM_DELAY_SECONDS"
kubectl -n "$NS" set env "deploy/$RELEASE-api" CHAOS_LLM_DELAY_SECONDS-
wait_rollout "$RELEASE-api"
echo "observing recovery for 5m ..."; sleep 300
write_window incident-1 llm-latency "$injected" "$recovered"
