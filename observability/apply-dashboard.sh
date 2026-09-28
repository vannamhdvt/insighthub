#!/usr/bin/env bash
# Load a dashboard JSON into Grafana via the sidecar.
#   observability/apply-dashboard.sh [namespace] [file]   (Day 6: monitoring llm-cost.json)
set -euo pipefail
ns=${1:-monitoring}
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
file=${2:-insighthub.json}
name=$([ "$file" = insighthub.json ] && echo insighthub-dashboard || echo "insighthub-${file%.json}-dashboard")
kubectl -n "$ns" create configmap "$name" \
  --from-file="$file"="$here/grafana-dashboards/$file" \
  --dry-run=client -o yaml | kubectl label --local -f - grafana_dashboard=1 -o yaml | kubectl apply -f -
