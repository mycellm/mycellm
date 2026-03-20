"""Activity tracking — real-time event stream and rolling statistics."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("mycellm.activity")


class EventType(str, Enum):
    INFERENCE_START = "inference_start"
    INFERENCE_COMPLETE = "inference_complete"
    INFERENCE_FAILED = "inference_failed"
    ROUTE_DECISION = "route_decision"
    PEER_CONNECTED = "peer_connected"
    PEER_DISCONNECTED = "peer_disconnected"
    MODEL_LOADED = "model_loaded"
    MODEL_UNLOADED = "model_unloaded"
    CREDIT_EARNED = "credit_earned"
    CREDIT_SPENT = "credit_spent"
    ANNOUNCE_OK = "announce_ok"
    ANNOUNCE_FAILED = "announce_failed"
    FLEET_NODE_JOINED = "fleet_node_joined"


@dataclass
class ActivityEvent:
    type: EventType
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "timestamp": self.timestamp,
            "time": time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
            **self.data,
        }


class ActivityTracker:
    """Tracks node activity with rolling stats and sparkline data."""

    def __init__(self, max_events: int = 1000, sparkline_minutes: int = 60):
        self._events: deque[ActivityEvent] = deque(maxlen=max_events)
        self._subscribers: list[asyncio.Queue] = []
        self._sparkline_minutes = sparkline_minutes

        # Rolling counters
        self._request_count = 0
        self._token_count = 0
        self._error_count = 0

        # Per-minute buckets for sparklines (circular buffer)
        self._minute_buckets: deque[dict] = deque(maxlen=sparkline_minutes)
        self._current_minute: int = 0
        self._current_bucket: dict = self._empty_bucket()

    def _empty_bucket(self) -> dict:
        return {"requests": 0, "tokens": 0, "errors": 0, "credits_earned": 0.0, "credits_spent": 0.0, "minute": 0}

    def _rotate_bucket(self) -> None:
        now_minute = int(time.time() / 60)
        if now_minute != self._current_minute:
            # Fill gaps with empty buckets
            gap = min(now_minute - self._current_minute, self._sparkline_minutes) if self._current_minute > 0 else 0
            if self._current_minute > 0:
                self._current_bucket["minute"] = self._current_minute
                self._minute_buckets.append(self._current_bucket)
                for _ in range(gap - 1):
                    self._minute_buckets.append(self._empty_bucket())
            self._current_minute = now_minute
            self._current_bucket = self._empty_bucket()

    def record(self, event_type: EventType, **data) -> ActivityEvent:
        """Record an activity event."""
        event = ActivityEvent(type=event_type, data=data)
        self._events.append(event)

        # Update rolling counters
        self._rotate_bucket()
        if event_type == EventType.INFERENCE_COMPLETE:
            self._request_count += 1
            self._current_bucket["requests"] += 1
            tokens = data.get("tokens", 0)
            self._token_count += tokens
            self._current_bucket["tokens"] += tokens
        elif event_type == EventType.INFERENCE_FAILED:
            self._error_count += 1
            self._current_bucket["errors"] += 1
        elif event_type == EventType.CREDIT_EARNED:
            self._current_bucket["credits_earned"] += data.get("amount", 0)
        elif event_type == EventType.CREDIT_SPENT:
            self._current_bucket["credits_spent"] += data.get("amount", 0)

        # Broadcast to SSE subscribers
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

        return event

    def recent(self, limit: int = 50, event_type: str | None = None) -> list[dict]:
        """Get recent events."""
        events = list(self._events)
        if event_type:
            events = [e for e in events if e.type.value == event_type]
        return [e.to_dict() for e in events[-limit:]]

    def stats(self) -> dict:
        """Get rolling statistics."""
        now = time.time()

        # Count events in last 1m and 5m
        req_1m = sum(1 for e in self._events if e.type == EventType.INFERENCE_COMPLETE and now - e.timestamp < 60)
        req_5m = sum(1 for e in self._events if e.type == EventType.INFERENCE_COMPLETE and now - e.timestamp < 300)
        tok_1m = sum(e.data.get("tokens", 0) for e in self._events if e.type == EventType.INFERENCE_COMPLETE and now - e.timestamp < 60)
        tok_5m = sum(e.data.get("tokens", 0) for e in self._events if e.type == EventType.INFERENCE_COMPLETE and now - e.timestamp < 300)
        err_5m = sum(1 for e in self._events if e.type == EventType.INFERENCE_FAILED and now - e.timestamp < 300)

        return {
            "total_requests": self._request_count,
            "total_tokens": self._token_count,
            "total_errors": self._error_count,
            "requests_per_min": req_1m,
            "requests_5min": req_5m,
            "tokens_per_min": tok_1m,
            "tokens_5min": tok_5m,
            "errors_5min": err_5m,
        }

    def sparkline(self, metric: str = "requests", minutes: int = 30) -> list[int | float]:
        """Get sparkline data for a metric over the last N minutes."""
        self._rotate_bucket()
        buckets = list(self._minute_buckets)[-minutes:]
        return [b.get(metric, 0) for b in buckets]

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass
