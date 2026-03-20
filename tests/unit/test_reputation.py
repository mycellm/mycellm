"""Unit tests for reputation tracking."""

import time
import pytest

from mycellm.accounting.reputation import PeerReputation, ReputationTracker


def test_new_peer_neutral_score():
    rep = PeerReputation(peer_id="test")
    assert rep.success_rate == 0.5  # neutral for new peers
    assert rep.score > 0  # should have a positive score


def test_record_success():
    rep = PeerReputation(peer_id="test")
    rep.record_success(tokens=100, response_time=0.5)
    assert rep.total_requests == 1
    assert rep.successful_requests == 1
    assert rep.tokens_served == 100
    assert rep.success_rate == 1.0


def test_record_failure():
    rep = PeerReputation(peer_id="test")
    rep.record_failure()
    assert rep.total_requests == 1
    assert rep.successful_requests == 0
    assert rep.success_rate == 0.0


def test_mixed_success_rate():
    rep = PeerReputation(peer_id="test")
    rep.record_success(50, 1.0)
    rep.record_success(50, 1.0)
    rep.record_failure()
    assert abs(rep.success_rate - 2 / 3) < 0.01


def test_avg_response_time():
    rep = PeerReputation(peer_id="test")
    rep.record_success(10, 1.0)
    rep.record_success(10, 3.0)
    assert rep.avg_response_time == 2.0


def test_receipt_bonus():
    rep1 = PeerReputation(peer_id="a")
    rep1.record_success(100, 1.0)
    score_no_receipts = rep1.score

    rep2 = PeerReputation(peer_id="b")
    rep2.record_success(100, 1.0)
    rep2.record_receipt()
    rep2.record_receipt()
    score_with_receipts = rep2.score

    assert score_with_receipts > score_no_receipts


def test_tracker_get_creates():
    tracker = ReputationTracker()
    rep = tracker.get("peer1")
    assert rep.peer_id == "peer1"


def test_tracker_record_and_score():
    tracker = ReputationTracker()
    tracker.record_success("peer1", 100, 0.5)
    assert tracker.score("peer1") > 0


def test_tracker_all_scores():
    tracker = ReputationTracker()
    tracker.record_success("peer1", 100, 0.5)
    tracker.record_success("peer2", 200, 1.0)
    scores = tracker.all_scores()
    assert len(scores) == 2


def test_reputation_to_dict():
    rep = PeerReputation(peer_id="test")
    rep.record_success(50, 1.0)
    d = rep.to_dict()
    assert d["peer_id"] == "test"
    assert d["tokens_served"] == 50
    assert d["successful_requests"] == 1
    assert "score" in d
