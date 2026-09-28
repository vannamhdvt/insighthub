#!/usr/bin/env python3
"""Xuất evidence Day 5 từ audit log của lượt demo Slack LIVE.

Chạy SAU khi code đã chốt (source_sha256 phủ cả file chưa commit):
    python3 chatops-bot/tools/export_evidence.py --audit chatops-bot/chatops-audit.log --since 2026-09-28T00:00:00Z
Ghi: evidence/day5/chatops-audit.json ({run_id, events}) và evidence/day5.json (envelope cho scripts/verify.py day5).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint() -> str:
    spec = importlib.util.spec_from_file_location("verify", ROOT / "scripts" / "verify.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.fingerprint(ROOT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", type=Path, default=ROOT / "chatops-bot" / "chatops-audit.log")
    ap.add_argument("--since", help="RFC3339; chỉ lấy event từ thời điểm này (lượt demo)")
    args = ap.parse_args()

    since = datetime.fromisoformat(args.since.replace("Z", "+00:00")).timestamp() if args.since else 0
    events = []
    for line in args.audit.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if "test_run_id" in event:  # bỏ event do pytest sinh ra
            continue
        if datetime.fromisoformat(event["timestamp"]).timestamp() >= since:
            events.append(event)
    decisions = {e["decision"] for e in events}
    missing = {"denied", "approval_required"} - decisions
    if missing:
        print(f"audit thiếu decision {sorted(missing)}: demo thêm 'scale' bởi non-operator và operator", file=sys.stderr)
        return 2
    if not any(e["action"].startswith("mcp:") and e["outcome"] == "ok" for e in events):
        print("audit chưa có MCP call thành công: kiểm tra Prometheus/K8s MCP trước khi xuất", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out_dir = ROOT / "evidence" / "day5"
    out_dir.mkdir(parents=True, exist_ok=True)
    audit_file = out_dir / "chatops-audit.json"
    audit_file.write_text(json.dumps({"run_id": "slack-live-" + now, "source": "chatops-bot/chatops-audit.log",
                                      "events": events}, ensure_ascii=False, indent=2) + "\n")
    permissions = ROOT / "chatops-bot" / "app" / "permissions.py"
    envelope = {
        "schema_version": 1,
        "day": 5,
        "mode": "real",
        "observed_at": now,
        "source_sha256": fingerprint(),
        "artifacts": {
            "permissions": {"path": "chatops-bot/app/permissions.py", "sha256": sha(permissions)},
            "audit": {"path": "evidence/day5/chatops-audit.json", "sha256": sha(audit_file)},
        },
    }
    (ROOT / "evidence" / "day5.json").write_text(json.dumps(envelope, indent=2) + "\n")
    counts = {d: sum(e["decision"] == d for e in events) for d in sorted(decisions)}
    print(f"wrote {audit_file.relative_to(ROOT)} ({len(events)} events {counts}) and evidence/day5.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
