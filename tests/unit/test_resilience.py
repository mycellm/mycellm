"""Tests for the heartbeat watchdog wedge-detection logic.

We exercise _check_once() directly (it returns an abort reason or None) so we
never spawn the thread or call os._exit().
"""

import time
from unittest.mock import patch

from mycellm.resilience import HeartbeatWatchdog


def _wd(**kw):
    kw.setdefault("stall_seconds", 30.0)
    kw.setdefault("fd_pct", 0.85)
    wd = HeartbeatWatchdog(**kw)
    wd._fd_limit = 0  # disable fd check unless a test sets it
    return wd


def test_fresh_heartbeat_is_healthy():
    wd = _wd()
    wd.beat()
    assert wd._check_once() is None


def test_stale_heartbeat_triggers_abort():
    wd = _wd(stall_seconds=10.0)
    wd._last_beat = time.monotonic() - 25  # 25s stale > 10s limit
    reason = wd._check_once()
    assert reason is not None and "stalled" in reason


def test_defer_suppresses_stall_check():
    wd = _wd(stall_seconds=10.0)
    wd._last_beat = time.monotonic() - 25
    wd.defer(60.0)  # known-slow op in progress
    assert wd._check_once() is None


def test_fd_ceiling_triggers_abort():
    wd = _wd()
    wd.beat()
    wd._fd_limit = 100
    with patch("mycellm.resilience._open_fd_count", return_value=90):  # 90 > 85
        reason = wd._check_once()
    assert reason is not None and "fds" in reason


def test_fd_below_ceiling_is_healthy():
    wd = _wd()
    wd.beat()
    wd._fd_limit = 100
    with patch("mycellm.resilience._open_fd_count", return_value=50):
        assert wd._check_once() is None


def test_fd_check_skipped_when_count_unavailable():
    wd = _wd()
    wd.beat()
    wd._fd_limit = 100
    with patch("mycellm.resilience._open_fd_count", return_value=-1):  # unknown
        assert wd._check_once() is None


def test_disabled_watchdog_start_is_noop():
    wd = _wd(enabled=False)
    wd.start()
    assert wd._thread is None
