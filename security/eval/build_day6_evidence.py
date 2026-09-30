#!/usr/bin/env python3
"""Write evidence/day6.json (envelope for scripts/verify.py day6) from the saved reports.

Run AFTER the code is final and both evals ran on that code:
  python3 security/eval/build_day6_evidence.py
Expects evidence/day6/eval-initial.json, eval-final.json, cost-final.json.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ART = {
    "dataset": "security/eval/dataset.json",
    "eval_initial": "evidence/day6/eval-initial.json",
    "eval_final": "evidence/day6/eval-final.json",
    "cost": "evidence/day6/cost-final.json",
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    spec = importlib.util.spec_from_file_location("verify", ROOT / "scripts" / "verify.py")
    verify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verify)  # type: ignore[union-attr]
    source = verify.fingerprint(ROOT)
    for role in ("eval_initial", "eval_final", "cost"):
        data = json.loads((ROOT / ART[role]).read_text())
        if data.get("source_sha256") != source:
            sys.exit(f"{ART[role]} was produced on different source; re-run run_eval.py on the final code")
    envelope = {
        "schema_version": 1, "day": 6, "mode": "real",
        "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_sha256": source,
        "artifacts": {role: {"path": path, "sha256": sha(ROOT / path)} for role, path in ART.items()},
    }
    (ROOT / "evidence" / "day6.json").write_text(json.dumps(envelope, indent=2) + "\n")
    print("wrote evidence/day6.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
