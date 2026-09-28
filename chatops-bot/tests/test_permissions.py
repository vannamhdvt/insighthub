import pytest

from app.intents import Intent
from app.permissions import ApprovalError, ApprovalStore, PermissionPolicy, load_scale_targets
from conftest import BOT, OPERATOR


@pytest.fixture
def policy():
    return PermissionPolicy(frozenset({OPERATOR}), load_scale_targets(BOT / "service-catalog.yaml"))


def test_read_is_auto_allowed_for_anyone(policy):
    for name in ("health", "ingest_today", "failing_pods"):
        assert policy.decide("URANDOM", Intent(name)).decision == "allowed"


def test_write_requires_operator(policy):
    d = policy.decide("URANDOM", Intent("scale", {"target": "api", "replicas": 3}))
    assert d.decision == "denied" and d.tier == "write"


def test_write_asks_confirmation(policy):
    d = policy.decide(OPERATOR, Intent("scale", {"target": "api", "replicas": 3}))
    assert d.decision == "approval_required"
    assert d.args == {"deployment": "insighthub-api", "replicas": 3}


@pytest.mark.parametrize("target,replicas", [("postgres", 2), ("redis", 2), ("kube-dns", 2), ("api", 0),
                                             ("api", 6), ("api", -1)])
def test_scale_outside_catalog_bounds_denied(policy, target, replicas):
    assert policy.decide(OPERATOR, Intent("scale", {"target": target, "replicas": replicas})).decision == "denied"


def test_destructive_always_denied_even_for_operator(policy):
    assert policy.decide(OPERATOR, Intent("destructive", {"verb": "delete"})).decision == "denied"


def test_unknown_action_treated_as_destructive(policy):
    assert policy.decide(OPERATOR, Intent("drop_database")).tier == "destructive"


def test_token_single_use_and_bound():
    clock = [1000.0]
    store = ApprovalStore(60, clock=lambda: clock[0])
    item = store.issue("scale", {"deployment": "insighthub-api", "replicas": 3}, OPERATOR, "C1")
    with pytest.raises(ApprovalError, match="different action"):
        store.redeem(item.token, OPERATOR, "C1", "scale", {"deployment": "insighthub-api", "replicas": 5})
    with pytest.raises(ApprovalError, match="another channel"):
        store.redeem(item.token, OPERATOR, "C2", "scale", item.args)
    store.redeem(item.token, OPERATOR, "C1", "scale", item.args)
    with pytest.raises(ApprovalError, match="already used"):
        store.redeem(item.token, OPERATOR, "C1", "scale", item.args)


def test_token_expires_after_ttl():
    clock = [1000.0]
    store = ApprovalStore(60, clock=lambda: clock[0])
    item = store.issue("scale", {"deployment": "insighthub-api", "replicas": 3}, OPERATOR, "C1")
    clock[0] += 61
    with pytest.raises(ApprovalError, match="expired"):
        store.redeem(item.token, OPERATOR, "C1", "scale", item.args)
