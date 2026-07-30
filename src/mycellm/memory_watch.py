"""Runtime memory-pressure watcher (macOS).

The preflight prevents loads that don't fit; this closes the runtime gap —
other apps, KV growth, or fragmentation can push a healthy node toward the
OS OOM killer, which hard-kills the process and trips the crash-loop guard.
Instead we react while still alive:

  warn      → free the MLX Metal buffer cache (cheap, no service impact)
  critical  → evict idle local models, newest-first survivor; a second
              consecutive critical tick evicts the last model too — a node
              serving nothing beats a dead node being restart-looped.

Pressure comes from ``sysctl kern.memorystatus_vm_pressure_level``
(1=normal, 2=warn, 4=critical). Non-Darwin hosts read 0 and the watcher
idles. Evicted models stay enabled in saved configs, so they return on the
next restart/restore.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess

logger = logging.getLogger("mycellm.memory")

NORMAL, WARN, CRITICAL = 1, 2, 4


def read_pressure_level() -> int:
    """Current kernel memory-pressure level; 0 when unavailable."""
    try:
        out = subprocess.run(
            ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
            capture_output=True, text=True, timeout=5,
        )
        return int(out.stdout.strip() or 0)
    except Exception:
        return 0


def clear_gpu_cache() -> bool:
    """Release the MLX Metal buffer cache back to the OS."""
    try:
        import mlx.core as mx

        freed = mx.get_cache_memory()
        mx.clear_cache()
        if freed:
            logger.info(f"Memory pressure: cleared {freed / 2**20:.0f}MB Metal cache")
        return True
    except Exception:
        return False


class MemoryPressureWatcher:
    """Polls kernel pressure and de-escalates before the OOM killer fires."""

    def __init__(
        self,
        inference,
        *,
        interval: float = 10.0,
        evict: bool = True,
        idle_seconds: float = 60.0,
        read_level=read_pressure_level,
        clear_cache=clear_gpu_cache,
    ):
        self._inference = inference
        self._interval = interval
        self._evict = evict
        self._idle_seconds = idle_seconds
        self._read_level = read_level
        self._clear_cache = clear_cache
        self._prev_critical = False
        self.events: int = 0  # ticks that took action (health/introspection)

    async def check_once(self) -> int:
        """One poll: read pressure, act. Returns the level seen."""
        level = self._read_level()
        if level < WARN:
            self._prev_critical = False
            return level

        self.events += 1
        self._clear_cache()
        if level >= CRITICAL and self._evict:
            # First critical tick spares the most recently used model; if the
            # kernel still reports critical next tick, nothing else is left to
            # give back — evict that one too rather than die to the OOM killer.
            evicted = await self._inference.evict_idle_models(
                self._idle_seconds, keep_most_recent=not self._prev_critical
            )
            if evicted:
                logger.error(
                    f"Memory pressure CRITICAL — evicted {evicted}; "
                    "models stay enabled and will restore on restart"
                )
            self._prev_critical = True
        else:
            self._prev_critical = False
            logger.warning("Memory pressure WARN — Metal cache cleared")
        return level

    async def run(self) -> None:
        logger.info(
            f"Memory-pressure watcher armed (interval {self._interval}s, "
            f"evict={'on' if self._evict else 'off'})"
        )
        while True:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"memory watch tick failed: {e}")
            await asyncio.sleep(self._interval)
