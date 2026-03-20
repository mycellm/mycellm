"""Unit tests for health metrics."""

import pytest

from mycellm.router.health import PeerHealthMetrics


def test_empty_metrics():
    m = PeerHealthMetrics()
    assert m.avg_rtt == 0.0
    assert m.success_rate == 1.0  # assume healthy
    assert m.health_score > 0


def test_record_success():
    m = PeerHealthMetrics()
    m.record_success(0.05)
    assert len(m.rtt_samples) == 1
    assert m.avg_rtt == 0.05
    assert m.success_rate == 1.0


def test_record_failure():
    m = PeerHealthMetrics()
    m.record_failure()
    assert m.success_rate == 0.0


def test_mixed_health():
    m = PeerHealthMetrics()
    for _ in range(8):
        m.record_success(0.1)
    for _ in range(2):
        m.record_failure()
    assert m.success_rate == 0.8
    assert m.health_score > 0.3


def test_rolling_window():
    m = PeerHealthMetrics()
    # Fill beyond max
    for i in range(25):
        m.record_success(0.1)
    assert len(m.rtt_samples) == 10  # capped
    assert len(m.success_history) == 20  # capped


def test_health_score_reliable_slow_beats_fast_flaky():
    reliable_slow = PeerHealthMetrics()
    for _ in range(10):
        reliable_slow.record_success(0.5)  # 500ms but always works

    fast_flaky = PeerHealthMetrics()
    for _ in range(5):
        fast_flaky.record_success(0.01)
    for _ in range(5):
        fast_flaky.record_failure()

    assert reliable_slow.health_score > fast_flaky.health_score
