#!/usr/bin/env bash
# Wrap observability/rules/insighthub-rules.yaml (the promtool-tested file) into a
# PrometheusRule and apply it. One source of truth: no hand-copied CR.
#   observability/apply-rules.sh [namespace] [rules-file]
#   default: monitoring insighthub-rules.yaml (CR insighthub-anomaly)
#   Day 6:   observability/apply-rules.sh monitoring llm-cost-rules.yaml (CR insighthub-llm-cost)
set -euo pipefail
ns=${1:-monitoring}
file=${2:-insighthub-rules.yaml}
case "$file" in insighthub-rules.yaml) cr=insighthub-anomaly ;; *) cr="insighthub-${file%-rules.yaml}" ;; esac
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 - "$here/rules/$file" "$ns" "$cr" <<'PY' | kubectl apply -f -
import sys
rules, ns, cr = sys.argv[1], sys.argv[2], sys.argv[3]
body = open(rules).read().splitlines()
start = next(i for i, l in enumerate(body) if l.startswith("groups:"))
print(f"""apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: {cr}
  namespace: {ns}
  labels:
    release: kube-prometheus-stack
    app.kubernetes.io/part-of: insighthub
spec:""")
for line in body[start:]:
    print("  " + line if line else "")
PY
kubectl -n "$ns" get prometheusrule "$cr"
