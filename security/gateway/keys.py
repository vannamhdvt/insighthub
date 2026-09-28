#!/usr/bin/env python3
"""Create/inspect the 3 LiteLLM virtual keys (stdlib only).

  python3 security/gateway/keys.py create   # idempotent: skips aliases that already exist
  python3 security/gateway/keys.py info     # spend / budget per key (no key values printed)

Env: LITELLM_URL (default http://127.0.0.1:4000), LITELLM_MASTER_KEY.
New key values are written ONLY to ~/.insighthub/gateway.env (chmod 600), never stdout.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

URL = os.environ.get("LITELLM_URL", "http://127.0.0.1:4000").rstrip("/")
MASTER = os.environ.get("LITELLM_MASTER_KEY", "")
ENV_FILE = Path(os.environ.get("INSIGHTHUB_GATEWAY_ENV", Path.home() / ".insighthub" / "gateway.env"))

# Budgets are per 30 days, in USD at list price. Sum = 3.0 USD for the whole lab.
WORKLOADS = {
    "insighthub": {
        "env": "INSIGHTHUB_LITELLM_KEY",
        "models": ["insighthub-chat", "insighthub-chat-local", "insighthub-embed"],
        "max_budget": 2.0, "rpm_limit": 60, "tpm_limit": 200_000,
    },
    "chatops-bot": {
        "env": "CHATOPS_LITELLM_KEY",
        "models": ["chatops-agent"],
        "max_budget": 0.5, "rpm_limit": 20, "tpm_limit": 60_000,
    },
    "coding": {
        "env": "CODING_LITELLM_KEY",
        "models": ["coding-review"],
        "max_budget": 0.5, "rpm_limit": 20, "tpm_limit": 100_000,
    },
}


def call(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(URL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {MASTER}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")


def existing_aliases() -> dict[str, dict]:
    data = call("GET", "/key/list?return_full_object=true&size=100")
    keys = data.get("keys", data if isinstance(data, list) else [])
    return {k.get("key_alias"): k for k in keys if isinstance(k, dict) and k.get("key_alias")}


def save_env(values: dict[str, str]) -> None:
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    kept = [ln for ln in lines if ln.split("=", 1)[0] not in values]
    kept += [f"{k}={v}" for k, v in values.items()]
    ENV_FILE.write_text("\n".join(kept) + "\n")
    ENV_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)


def create() -> None:
    have = existing_aliases()
    new: dict[str, str] = {}
    for alias, spec in WORKLOADS.items():
        if alias in have:
            print(f"= {alias}: exists (spend={have[alias].get('spend')}, max_budget={have[alias].get('max_budget')})")
            continue
        res = call("POST", "/key/generate", {
            "key_alias": alias, "models": spec["models"], "max_budget": spec["max_budget"],
            "budget_duration": "30d", "rpm_limit": spec["rpm_limit"], "tpm_limit": spec["tpm_limit"],
            "metadata": {"workload": alias, "tags": [f"workload:{alias}"]},
        })
        new[spec["env"]] = res["key"]
        print(f"+ {alias}: created, max_budget={spec['max_budget']} USD/30d, models={spec['models']}")
    if new:
        save_env(new)
        print(f"key values saved to {ENV_FILE} (chmod 600)")


def info() -> None:
    for alias, k in sorted(existing_aliases().items()):
        print(f"{alias:12s} spend={k.get('spend', 0):.6f} max_budget={k.get('max_budget')} "
              f"models={k.get('models')} rpm={k.get('rpm_limit')} tpm={k.get('tpm_limit')}")


if __name__ == "__main__":
    if not MASTER:
        sys.exit("LITELLM_MASTER_KEY is required (source ~/.insighthub/gateway.env)")
    {"create": create, "info": info}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
