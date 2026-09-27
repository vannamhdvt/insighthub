#!/usr/bin/env bash
# Wrap observability/rules/insighthub-rules.yaml (the promtool-tested file) into a
# PrometheusRule and apply it. One source of truth: no hand-copied CR.
#   observability/apply-rules.sh [namespace]   (default: monitoring)
set -euo pipefail
ns=${1:-monitoring}
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 - "$here/rules/insighthub-rules.yaml" "$ns" <<'PY' | kubectl apply -f -
import sys
rules, ns = sys.argv[1], sys.argv[2]
body = open(rules).read().splitlines()
start = next(i for i, l in enumerate(body) if l.startswith("groups:"))
print(f"""apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: insighthub-anomaly
  namespace: {ns}
  labels:
    release: kube-prometheus-stack
    app.kubernetes.io/part-of: insighthub
spec:""")
for line in body[start:]:
    print("  " + line if line else "")
PY
kubectl -n "$ns" get prometheusrule insighthub-anomaly
