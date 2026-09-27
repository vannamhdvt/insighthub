# Shared helpers for Day 4 incident drills. Sourced, not executed.
# Every drill writes evidence/day4/<incident>.window.json with RFC3339 UTC times:
#   baseline_start (10m before injection), injected_at, recovered_at, ended_at.
set -euo pipefail
NS=${NS:-insighthub-local}
RELEASE=${RELEASE:-insighthub}
DURATION=${DURATION:-10m}
ROOT=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
OUT="$ROOT/evidence/day4"
mkdir -p "$OUT"

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
minus_10m() { python3 -c 'import datetime as d,sys;t=d.datetime.strptime(sys.argv[1],"%Y-%m-%dT%H:%M:%SZ");print((t-d.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$1"; }
to_seconds() { python3 -c 'import sys,re;m=re.fullmatch(r"(\d+)([smh]?)",sys.argv[1]);print(int(m[1])*{"":1,"s":1,"m":60,"h":3600}[m[2]])' "$1"; }

write_window() { # incident_id kind injected recovered
  cat > "$OUT/$1.window.json" <<JSON
{
  "incident_id": "$1",
  "kind": "$2",
  "namespace": "$NS",
  "baseline_start": "$(minus_10m "$3")",
  "injected_at": "$3",
  "recovered_at": "$4",
  "ended_at": "$(now)"
}
JSON
  echo "window -> $OUT/$1.window.json"; cat "$OUT/$1.window.json"
}

wait_rollout() { kubectl -n "$NS" rollout status "deploy/$1" --timeout=5m; }

require_loadgen() {
  echo "Reminder: observability/loadgen.py must be running (baseline >= 1h before the first drill)."
}
