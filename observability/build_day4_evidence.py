#!/usr/bin/env python3
"""Write evidence/day4.json for `scripts/verify.py day4` from the committed artifacts.

Run it last, after the three RCA reports are final (hashes and the source
fingerprint are recorded; any later edit makes the verifier report a mismatch).

    python3 observability/build_day4_evidence.py
    python3 scripts/verify.py day4 --evidence-dir evidence --prometheus-url http://localhost:9090
"""

import datetime as dt
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ARTIFACTS = {
    "dashboard": "observability/grafana-dashboards/insighthub.json",
    "mlops_notes": "observability/mlops-overview-notes.md",
    "rca": "rca-reports/incident-1.json",
    "rca_2": "rca-reports/incident-2.json",
    "rca_3": "rca-reports/incident-3.json",
    "rules": "observability/rules/insighthub-rules.yaml",
    "rule_tests": "observability/rules/insighthub-rules.test.yaml",
}


def main():
    missing = [p for p in ARTIFACTS.values() if not (ROOT / p).is_file()]
    if missing:
        sys.exit("missing artifacts: " + ", ".join(missing))
    fingerprint = subprocess.check_output([sys.executable, "-B", str(ROOT / "scripts/verify.py"), "fingerprint", "--repo", str(ROOT)], text=True).strip()
    data = {
        "schema_version": 1,
        "day": 4,
        "mode": "real",
        "observed_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_sha256": fingerprint,
        "artifacts": {role: {"path": path, "sha256": hashlib.sha256((ROOT / path).read_bytes()).hexdigest()} for role, path in ARTIFACTS.items()},
    }
    out = ROOT / "evidence/day4.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n")
    print("wrote", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
