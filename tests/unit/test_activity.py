"""Unit tests for ActivityTracker."""

import time
import pytest

from mycellm.activity import ActivityTracker, EventType, ActivityEvent


def test_record_and_recent():
    t = ActivityTracker()
    t.record(EventType.INFERENCE_COMPLETE, model="llama-7b", tokens=50)
    t.record(EventType.ANNOUNCE_OK, target="bootstrap")

    events = t.recent()
    assert len(events) == 2
    assert events[0]["type"] == "inference_complete"
    assert events[1]["type"] == "announce_ok"


def test_stats_counting():
    t = ActivityTracker()
    t.record(EventType.INFERENCE_COMPLETE, tokens=100)
    t.record(EventType.INFERENCE_COMPLETE, tokens=200)
    t.record(EventType.INFERENCE_FAILED, error="timeout")

    stats = t.stats()
    assert stats["total_requests"] == 2
    assert stats["total_tokens"] == 300
    assert stats["total_errors"] == 1
    assert stats["requests_per_min"] == 2
    assert stats["errors_5min"] == 1


def test_recent_limit():
    t = ActivityTracker()
    for i in range(20):
        t.record(EventType.ANNOUNCE_OK, i=i)

    assert len(t.recent(limit=5)) == 5
    assert len(t.recent(limit=50)) == 20


def test_recent_filter_by_type():
    t = ActivityTracker()
    t.record(EventType.INFERENCE_COMPLETE, tokens=10)
    t.record(EventType.ANNOUNCE_OK)
    t.record(EventType.INFERENCE_COMPLETE, tokens=20)

    filtered = t.recent(event_type="inference_complete")
    assert len(filtered) == 2
    assert all(e["type"] == "inference_complete" for e in filtered)


def test_max_events_bounded():
    t = ActivityTracker(max_events=10)
    for i in range(25):
        t.record(EventType.ANNOUNCE_OK, i=i)

    assert len(t.recent(limit=100)) == 10


def test_sparkline_empty():
    t = ActivityTracker()
    data = t.sparkline("requests", 5)
    assert isinstance(data, list)


def test_credit_tracking():
    t = ActivityTracker()
    t.record(EventType.CREDIT_EARNED, amount=1.5)
    t.record(EventType.CREDIT_SPENT, amount=0.5)

    stats = t.stats()
    assert stats["total_requests"] == 0  # credits don't count as requests


def test_event_to_dict():
    e = ActivityEvent(type=EventType.INFERENCE_COMPLETE, data={"model": "test", "tokens": 42})
    d = e.to_dict()
    assert d["type"] == "inference_complete"
    assert d["model"] == "test"
    assert d["tokens"] == 42
    assert "time" in d
    assert "timestamp" in d


def test_all_event_types_serializable():
    t = ActivityTracker()
    for et in EventType:
        t.record(et, test=True)
    events = t.recent(limit=100)
    assert len(events) == len(EventType)


def test_tps_calculation():
    t = ActivityTracker()
    # Simulate 100 tokens in one inference
    t.record(EventType.INFERENCE_COMPLETE, tokens=100, latency_ms=500)
    assert t.tps > 0  # 100 tokens in last 60s = ~1.67 tps


def test_avg_latency():
    t = ActivityTracker()
    t.record(EventType.INFERENCE_COMPLETE, tokens=10, latency_ms=100)
    t.record(EventType.INFERENCE_COMPLETE, tokens=10, latency_ms=200)
    assert t.avg_latency_ms == 150.0


def test_stats_includes_tps():
    t = ActivityTracker()
    t.record(EventType.INFERENCE_COMPLETE, tokens=50, latency_ms=200)
    stats = t.stats()
    assert "tps" in stats
    assert "avg_latency_ms" in stats
