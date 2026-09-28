import time

import pytest

from app.signature import SignatureError, compute_signature, verify_slack_signature

SECRET = "s3cr3t"
BODY = b'{"type":"event_callback","event_id":"Ev1"}'


def test_valid_signature_passes():
    ts = str(int(time.time()))
    verify_slack_signature(SECRET, ts, compute_signature(SECRET, ts, BODY), BODY)


@pytest.mark.parametrize("mutate", [
    lambda ts, sig, body: (ts, sig, body + b" "),            # body changed (e.g. re-serialized JSON)
    lambda ts, sig, body: (ts, "v0=" + "0" * 64, body),      # wrong digest
    lambda ts, sig, body: (ts, None, body),                  # missing header
    lambda ts, sig, body: ("abc", sig, body),                # non numeric timestamp
])
def test_invalid_signature_rejected(mutate):
    ts = str(int(time.time()))
    t, s, b = mutate(ts, compute_signature(SECRET, ts, BODY), BODY)
    with pytest.raises(SignatureError):
        verify_slack_signature(SECRET, t, s, b)


@pytest.mark.parametrize("skew", [301, -301, 3600])
def test_replay_older_than_5_minutes_rejected(skew):
    now = time.time()
    ts = str(int(now - skew))
    with pytest.raises(SignatureError, match="stale"):
        verify_slack_signature(SECRET, ts, compute_signature(SECRET, ts, BODY), BODY, now=now)


def test_missing_secret_fails_closed():
    ts = str(int(time.time()))
    with pytest.raises(SignatureError):
        verify_slack_signature("", ts, compute_signature("", ts, BODY), BODY)
