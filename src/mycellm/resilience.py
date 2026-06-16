"""Process-level resilience: an out-of-loop heartbeat watchdog.

A wedged asyncio event loop — file-descriptor exhaustion (EMFILE), a blocking
synchronous call, a GIL-holding model load that never returns, a deadlock —
leaves the process *alive but unable to serve*. Platform supervisors (launchd
``KeepAlive``, systemd ``Restart=on-failure``, Docker ``restart:``) only restart
on process *exit*, so a hang is invisible to them and the node sits there dead.

``HeartbeatWatchdog`` closes that gap. It runs in a dedicated OS thread — **not**
an asyncio task, because a task cannot run while the loop it lives on is blocked.
The event loop bumps a monotonic heartbeat on a fast timer; the thread checks,
independently of the loop, that the heartbeat is fresh and that the process is
below its fd ceiling. On a breach it writes a diagnostic line and calls
``os._exit(EXIT_CODE)`` — converting an invisible hang into a clean non-zero exit
the supervisor already knows how to restart.

This is deliberately blunt: ``os._exit`` skips atexit/flush so it works even when
the interpreter is wedged. It is the *last* line of defence; fixing the root
cause of a hang is always preferable.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

logger = logging.getLogger(__name__)

# Non-zero so launchd KeepAlive{SuccessfulExit:false} / systemd Restart=on-failure
# treat it as a failure and relaunch. 70 == EX_SOFTWARE (sysexits.h).
WATCHDOG_EXIT_CODE = 70


def _open_fd_count() -> int:
    """Best-effort count of open file descriptors for this process.

    Works on Linux (``/proc/self/fd``) and macOS/BSD (``/dev/fd``). Returns -1 if
    neither is available so callers can skip the fd check rather than false-fire.
    """
    for path in ("/proc/self/fd", "/dev/fd"):
        try:
            return len(os.listdir(path))
        except OSError:
            continue
    return -1


def _fd_limit() -> int:
    try:
        import resource

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        # RLIM_INFINITY (-1) or absurdly large → treat as "no meaningful ceiling".
        for lim in (soft, hard):
            if 0 < lim < 10_000_000:
                return lim
    except Exception:
        pass
    return 0


class HeartbeatWatchdog:
    """Watches loop liveness + fd usage from an OS thread; exits on a wedge.

    Usage:
        wd = HeartbeatWatchdog(log_path=..., stall_seconds=90)
        wd.start()
        # from the event loop, on a fast timer:
        wd.beat()
        # around a known-slow blocking op that legitimately stalls the loop:
        wd.defer(seconds=load_timeout)
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        stall_seconds: float = 90.0,
        fd_pct: float = 0.85,
        check_interval: float = 5.0,
        log_path: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.stall_seconds = max(10.0, float(stall_seconds))
        self.fd_pct = min(0.99, max(0.5, float(fd_pct)))
        self.check_interval = max(1.0, float(check_interval))
        self.log_path = log_path

        self._last_beat = time.monotonic()
        self._defer_until = 0.0
        self._fd_limit = _fd_limit()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- called from the event loop -------------------------------------------

    def beat(self) -> None:
        """Record loop liveness. Cheap; safe to call often."""
        self._last_beat = time.monotonic()

    def defer(self, seconds: float) -> None:
        """Suppress the *stall* check for ``seconds`` (a known-slow blocking op).

        The fd-ceiling check still runs — it is always valid. Use this around
        large synchronous model loads that can legitimately block the loop.
        """
        self._defer_until = max(self._defer_until, time.monotonic() + max(0.0, seconds))

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        if not self.enabled:
            logger.debug("Heartbeat watchdog disabled")
            return
        if self._thread is not None:
            return
        self._last_beat = time.monotonic()
        self._thread = threading.Thread(
            target=self._run, name="mycellm-watchdog", daemon=True
        )
        self._thread.start()
        logger.info(
            "Heartbeat watchdog armed (stall>%.0fs, fds>%.0f%% of %s) → os._exit(%d) on wedge",
            self.stall_seconds,
            self.fd_pct * 100,
            self._fd_limit or "?",
            WATCHDOG_EXIT_CODE,
        )

    def stop(self) -> None:
        self._stop.set()

    # -- thread body ----------------------------------------------------------

    def _abort(self, reason: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = (
            f"{ts} mycellm watchdog ABORT pid={os.getpid()} {reason} "
            f"(exit {WATCHDOG_EXIT_CODE}; supervisor will restart)\n"
        )
        try:
            sys.stderr.write(line)
            sys.stderr.flush()
        except Exception:
            pass
        if self.log_path:
            try:
                with open(self.log_path, "a") as fh:
                    fh.write(line)
            except Exception:
                pass
        # os._exit, not sys.exit: skip atexit/flush hooks that may themselves be
        # wedged. The supervisor restarts us cleanly.
        os._exit(WATCHDOG_EXIT_CODE)

    def _check_once(self) -> str | None:
        """Return an abort reason if the process looks wedged, else None.

        Pure-ish (reads clock + fd count); separated from ``_run`` so it can be
        unit-tested without spawning a thread or calling os._exit.
        """
        now = time.monotonic()

        # 1) Loop liveness — heartbeat must be fresh (unless deferred for a
        #    known-slow op). A stale heartbeat means the event loop is blocked.
        if now >= self._defer_until:
            age = now - self._last_beat
            if age > self.stall_seconds:
                return f"event loop stalled {age:.0f}s (limit {self.stall_seconds:.0f}s)"

        # 2) fd ceiling — preempt EMFILE so the loop never wedges on accept().
        if self._fd_limit > 0:
            fds = _open_fd_count()
            if fds > 0 and fds > self._fd_limit * self.fd_pct:
                return f"open fds {fds}/{self._fd_limit} over {self.fd_pct:.0%} ceiling"

        return None

    def _run(self) -> None:
        while not self._stop.wait(self.check_interval):
            reason = self._check_once()
            if reason:
                self._abort(reason)
