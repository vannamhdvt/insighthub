#!/usr/bin/env bash
# Load observability/grafana-dashboards/insighthub.json into Grafana via the sidecar.
set -euo pipefail
ns=${1:-monitoring}
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
kubectl -n "$ns" create configmap insighthub-dashboard \
  --from-file=insighthub.json="$here/grafana-dashboards/insighthub.json" \
  --dry-run=client -o yaml | kubectl label --local -f - grafana_dashboard=1 -o yaml | kubectl apply -f -
