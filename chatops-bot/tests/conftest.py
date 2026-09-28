from __future__ import annotations

import sys
from pathlib import Path

import pytest

BOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOT))
sys.path.insert(0, str(BOT / "tests"))

from app.config import Settings  # noqa: E402

SECRET = "test-signing-secret"
OPERATOR = "UOPS1"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        signing_secret=SECRET,
        bot_token="xoxb-test",
        bot_user_id="UBOT",
        operator_ids=frozenset({OPERATOR}),
        audit_log=tmp_path / "chatops-audit.log",
        queue_db=tmp_path / "queue.sqlite3",
        catalog=BOT / "service-catalog.yaml",
        scaler_kubeconfig="/dev/null",
    )
