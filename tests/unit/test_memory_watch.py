"""Memory-pressure watcher: WARN clears the Metal cache, CRITICAL evicts idle
models newest-first-survivor, and a second consecutive CRITICAL gives up the
last model too. Eviction leaves saved configs enabled so evicted models come
back on restart — unlike a user-initiated unload.
"""

import asyncio

from mycellm.inference.manager import InferenceManager
from mycellm.memory_watch import CRITICAL, NORMAL, WARN, MemoryPressureWatcher
from mycellm.protocol.capabilities import ModelCapability


class _FakeBackend:
    def __init__(self):
        self.unloaded = []

    async def unload_model(self, name):
        self.unloaded.append(name)


def _manager_with_models(*names, backend="mlx", last_used=None):
    m = InferenceManager()
    be = _FakeBackend()
    for i, n in enumerate(names):
        m._backends[n] = be
        m._model_info[n] = ModelCapability(name=n, backend=backend)
        m._saved_configs[n] = {"name": n, "enabled": True}
        m._last_used[n] = (last_used or {}).get(n, 100.0 + i)
    return m, be


def _watcher(m, levels, evict=True):
    seq = iter(levels)
    cleared = []
    w = MemoryPressureWatcher(
        m, evict=evict, idle_seconds=0.0,
        read_level=lambda: next(seq),
        clear_cache=lambda: cleared.append(True) or True,
    )
    return w, cleared


class TestEvictIdleModels:
    def test_keeps_most_recently_used(self):
        m, be = _manager_with_models("old", "mid", "new",
                                     last_used={"old": 1, "mid": 2, "new": 3})
        evicted = asyncio.run(m.evict_idle_models(0.0))
        assert evicted == ["old", "mid"]
        assert "new" in m._backends
        # Evicted models stay enabled → restored on next boot.
        assert m._saved_configs["old"]["enabled"] is True

    def test_busy_model_never_evicted(self):
        m, be = _manager_with_models("busy", "idle")
        m._queue_depth["busy"] = 2
        evicted = asyncio.run(m.evict_idle_models(0.0, keep_most_recent=False))
        assert "busy" not in evicted

    def test_remote_backends_ignored(self):
        m, be = _manager_with_models("api-model", backend="openai")
        assert asyncio.run(m.evict_idle_models(0.0, keep_most_recent=False)) == []


class TestWatcher:
    def test_normal_does_nothing(self):
        m, _ = _manager_with_models("m1")
        w, cleared = _watcher(m, [NORMAL])
        assert asyncio.run(w.check_once()) == NORMAL
        assert not cleared and w.events == 0

    def test_warn_clears_cache_only(self):
        m, _ = _manager_with_models("m1")
        w, cleared = _watcher(m, [WARN])
        asyncio.run(w.check_once())
        assert cleared and "m1" in m._backends

    def test_critical_spares_newest_then_gives_it_up(self):
        m, be = _manager_with_models("old", "new", last_used={"old": 1, "new": 2})

        async def scenario():
            w, _ = _watcher(m, [CRITICAL, CRITICAL])
            await w.check_once()
            assert be.unloaded == ["old"]  # first tick spares the newest
            await w.check_once()
            assert be.unloaded == ["old", "new"]  # still critical → all-in

        asyncio.run(scenario())

    def test_recovery_resets_escalation(self):
        m, be = _manager_with_models("old", "new", last_used={"old": 1, "new": 2})

        async def scenario():
            w, _ = _watcher(m, [CRITICAL, NORMAL, CRITICAL])
            await w.check_once()
            await w.check_once()  # normal tick resets
            await w.check_once()  # critical again — but only "new" is left...
            # ...and it is spared again because escalation was reset.
            assert be.unloaded == ["old"]

        asyncio.run(scenario())

    def test_evict_disabled(self):
        m, be = _manager_with_models("m1")
        w, cleared = _watcher(m, [CRITICAL], evict=False)
        asyncio.run(w.check_once())
        assert cleared and be.unloaded == []
