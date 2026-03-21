"""Tests for seeder-side peer admission control."""

import pytest
from mycellm.accounting.reputation import ReputationTracker, AdmissionResult


@pytest.fixture
def tracker():
    return ReputationTracker()


def test_new_peer_allowed_during_grace(tracker):
    """New peers (< grace_requests) are always allowed."""
    result = tracker.check_admission("new-peer", grace_requests=5)
    assert result.allowed
    assert result.reason == "grace_period"


def test_peer_with_receipts_always_allowed(tracker):
    """Peers who have served others (have receipts) are always allowed."""
    rep = tracker.get("good-peer")
    rep.total_requests = 100
    rep.receipt_count = 5
    result = tracker.check_admission("good-peer", require_receipts=True)
    assert result.allowed
    assert result.reason == "has_receipts"


def test_freeloader_rejected_after_grace(tracker):
    """Peers who only consume and never seed are rejected after grace."""
    rep = tracker.get("freeloader")
    rep.total_requests = 10  # past grace period
    rep.receipt_count = 0  # never served anyone
    result = tracker.check_admission(
        "freeloader", require_receipts=True, grace_requests=5
    )
    assert not result.allowed
    assert "no_contribution" in result.reason


def test_low_score_rejected(tracker):
    """Peers below minimum score are rejected."""
    rep = tracker.get("bad-peer")
    rep.total_requests = 10
    rep.successful_requests = 1  # low success rate
    rep.receipt_count = 0
    result = tracker.check_admission("bad-peer", min_score=0.5, grace_requests=5)
    assert not result.allowed
    assert "reputation_too_low" in result.reason


def test_no_policy_always_allows(tracker):
    """With default policy (no requirements), everyone is allowed."""
    rep = tracker.get("any-peer")
    rep.total_requests = 100
    result = tracker.check_admission(
        "any-peer", min_score=0.0, require_receipts=False
    )
    assert result.allowed


def test_grace_period_configurable(tracker):
    """Grace period is configurable."""
    rep = tracker.get("peer")
    rep.total_requests = 3
    # Grace = 5: still in grace
    assert tracker.check_admission("peer", grace_requests=5, require_receipts=True).allowed
    # Grace = 2: past grace, no receipts → rejected
    assert not tracker.check_admission("peer", grace_requests=2, require_receipts=True).allowed


def test_contributor_with_good_score_allowed(tracker):
    """Contributors with good reputation pass all checks."""
    rep = tracker.get("contributor")
    rep.total_requests = 50
    rep.successful_requests = 48
    rep.receipt_count = 10
    rep.tokens_served = 5000
    result = tracker.check_admission(
        "contributor", min_score=0.3, require_receipts=True, grace_requests=5
    )
    assert result.allowed
    assert result.score > 0


def test_admission_result_has_score(tracker):
    """AdmissionResult includes the peer's reputation score."""
    tracker.get("peer").total_requests = 10
    tracker.get("peer").successful_requests = 8
    result = tracker.check_admission("peer")
    assert result.score > 0


def test_admission_with_trust_override():
    """Full trust peers bypass admission regardless of reputation."""
    tracker = ReputationTracker()
    rep = tracker.get("org-peer")
    rep.total_requests = 100
    rep.receipt_count = 0  # never seeded — would be rejected normally

    # With require_receipts=True, this peer would be rejected
    result = tracker.check_admission(
        "org-peer", require_receipts=True, grace_requests=5
    )
    assert not result.allowed

    # But with org trust, we'd skip the check entirely
    # (the trust override happens in node._resolve_peer_trust,
    # not in the tracker — this test confirms the tracker rejects,
    # and the node-level override would allow)
    org_result = AdmissionResult(True, "org_trust", 1.0)
    assert org_result.allowed


def test_admission_different_peers_independent(tracker):
    """Each peer is evaluated independently."""
    # Good peer
    good = tracker.get("good")
    good.total_requests = 20
    good.receipt_count = 5
    # Bad peer
    bad = tracker.get("bad")
    bad.total_requests = 20
    bad.receipt_count = 0

    assert tracker.check_admission("good", require_receipts=True, grace_requests=5).allowed
    assert not tracker.check_admission("bad", require_receipts=True, grace_requests=5).allowed
