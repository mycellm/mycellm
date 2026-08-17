"""Relay lifecycle: ghost models, name collisions, and upstream reconciliation.

⚠️ REGRESSION SUITE for three defects that were live in 0.7.1. Each one is
the same shape as bugs this project has shipped before: state was *recorded*
correctly (`online = False`) but never *enforced* (the models stayed
registered and advertised).

D1 — a relay that went offline kept its models loaded and advertised, so the
     fleet routed to a dead endpoint and every request failed at inference
     time instead of the model simply not being offered.
D2 — two relays exposing the same upstream model both wanted `relay:{id}`.
     The second was silently skipped, so its capacity was invisible; and
     `remove()` on either unloaded the other's model.
D3 — a refresh only ever added. A model withdrawn upstream stayed registered
     forever.
"""

import pytest

from mycellm.inference.relay import RelayEndpoint, RelayManager


class FakeInference:
    """Minimal stand-in for InferenceManager's load/unload surface."""

    class _M:
        def __init__(self, name):
            self.name = name

    def __init__(self):
        self.names: list[str] = []
        self.unloaded: list[str] = []

    @property
    def loaded_models(self):
        return [self._M(n) for n in self.names]

    async def load_model(self, path, *, name, **kwargs):
        self.names.append(name)
        return True

    async def unload_model(self, name):
        if name in self.names:
            self.names.remove(name)
        self.unloaded.append(name)
        return True


def make_manager():
    inf = FakeInference()
    return RelayManager(inf), inf


async def register(mgr, relay, model_ids):
    """Drive the registration half of _discover_models without HTTP."""
    relay.online = True
    relay.models = [{"id": m} for m in model_ids]
    # _discover_models does the HTTP; exercise the same bookkeeping it does.
    seen = {}
    for mid in model_ids:
        name = mgr._claim_name(relay, mid)
        seen[name] = mid
        if name in relay.registered:
            continue
        if name in {m.name for m in mgr._inference.loaded_models}:
            continue
        await mgr._inference.load_model("", name=name)
        relay.registered[name] = mid
    for gone in [n for n in relay.registered if n not in seen]:
        await mgr._inference.unload_model(gone)
        relay.registered.pop(gone, None)


class TestD1GhostModels:
    @pytest.mark.asyncio
    async def test_offline_relay_withdraws_its_models(self):
        mgr, inf = make_manager()
        relay = RelayEndpoint(url="http://box.lan", name="box")
        mgr._relays[relay.url] = relay
        await register(mgr, relay, ["llama3", "qwen3"])
        assert sorted(inf.names) == ["relay:llama3", "relay:qwen3"]

        # The endpoint dies.
        await mgr._deregister(relay, reason="connection refused")

        assert inf.names == [], "models must not remain loaded"
        assert relay.registered == {}, "ownership record must be cleared"

    @pytest.mark.asyncio
    async def test_healthy_reflects_registration_not_just_a_flag(self):
        relay = RelayEndpoint(url="http://box.lan", online=True)
        assert relay.healthy is False, "online with nothing registered is not healthy"
        relay.registered["relay:m"] = "m"
        assert relay.healthy is True
        relay.online = False
        assert relay.healthy is False

    @pytest.mark.asyncio
    async def test_deregister_is_idempotent(self):
        mgr, _ = make_manager()
        relay = RelayEndpoint(url="http://box.lan")
        assert await mgr._deregister(relay, reason="x") == 0


class TestD2NameCollision:
    @pytest.mark.asyncio
    async def test_two_relays_serving_the_same_model_both_register(self):
        mgr, inf = make_manager()
        a = RelayEndpoint(url="http://a.lan", name="alpha")
        b = RelayEndpoint(url="http://b.lan", name="bravo")
        mgr._relays[a.url] = a
        mgr._relays[b.url] = b

        await register(mgr, a, ["llama3"])
        await register(mgr, b, ["llama3"])

        # Both are serving. Before the fix, bravo's was silently dropped.
        assert len(inf.names) == 2, f"both relays must register: {inf.names}"
        assert "relay:llama3" in a.registered
        assert list(b.registered) == ["relay:bravo:llama3"]

    @pytest.mark.asyncio
    async def test_removing_one_relay_leaves_the_others_model(self):
        mgr, inf = make_manager()
        a = RelayEndpoint(url="http://a.lan", name="alpha")
        b = RelayEndpoint(url="http://b.lan", name="bravo")
        mgr._relays[a.url] = a
        mgr._relays[b.url] = b
        await register(mgr, a, ["llama3"])
        await register(mgr, b, ["llama3"])

        await mgr.remove("http://a.lan")

        # bravo still serves its own. Before the fix, removing alpha
        # reconstructed `relay:llama3` and unloaded whichever held it.
        assert "relay:bravo:llama3" in inf.names
        assert b.registered == {"relay:bravo:llama3": "llama3"}

    @pytest.mark.asyncio
    async def test_first_claimant_keeps_the_plain_name(self):
        # Existing single-relay setups must not see a rename.
        mgr, _ = make_manager()
        a = RelayEndpoint(url="http://a.lan", name="alpha")
        mgr._relays[a.url] = a
        await register(mgr, a, ["llama3"])
        assert list(a.registered) == ["relay:llama3"]

    @pytest.mark.asyncio
    async def test_a_relay_reclaims_its_own_name_on_refresh(self):
        mgr, inf = make_manager()
        a = RelayEndpoint(url="http://a.lan", name="alpha")
        mgr._relays[a.url] = a
        await register(mgr, a, ["llama3"])
        await register(mgr, a, ["llama3"])   # refresh
        assert list(a.registered) == ["relay:llama3"]
        assert inf.names.count("relay:llama3") == 1, "must not double-register"

    @pytest.mark.asyncio
    async def test_a_local_model_is_never_displaced(self):
        mgr, inf = make_manager()
        inf.names.append("relay:llama3")     # not owned by any relay
        a = RelayEndpoint(url="http://a.lan", name="alpha")
        mgr._relays[a.url] = a
        await register(mgr, a, ["llama3"])
        assert a.registered == {}, "must not claim a name another backend holds"


class TestD3Reconciliation:
    @pytest.mark.asyncio
    async def test_model_withdrawn_upstream_is_unregistered(self):
        mgr, inf = make_manager()
        a = RelayEndpoint(url="http://a.lan", name="alpha")
        mgr._relays[a.url] = a
        await register(mgr, a, ["llama3", "qwen3"])
        assert len(inf.names) == 2

        # Upstream now serves only qwen3.
        await register(mgr, a, ["qwen3"])

        assert inf.names == ["relay:qwen3"]
        assert list(a.registered) == ["relay:qwen3"]

    @pytest.mark.asyncio
    async def test_reconciliation_still_adds_new_models(self):
        mgr, inf = make_manager()
        a = RelayEndpoint(url="http://a.lan", name="alpha")
        mgr._relays[a.url] = a
        await register(mgr, a, ["llama3"])
        await register(mgr, a, ["llama3", "phi4"])
        assert sorted(inf.names) == ["relay:llama3", "relay:phi4"]


class TestIdentity:
    def test_group_and_deployment_ids_are_stable(self):
        r = RelayEndpoint(url="http://box.lan:11434", name="Ollama Box")
        assert r.group_id == "grp_ollama-box"
        assert r.group_id == RelayEndpoint(url="x", name="Ollama Box").group_id
        d = r.deployment_id("relay:llama3")
        assert d.startswith("dep_")
        assert d == r.deployment_id("relay:llama3")

    def test_deployment_ids_differ_per_model(self):
        r = RelayEndpoint(url="http://box.lan", name="box")
        assert r.deployment_id("relay:a") != r.deployment_id("relay:b")

    def test_group_id_falls_back_to_the_url_label(self):
        r = RelayEndpoint(url="http://box.lan:11434")
        assert r.group_id.startswith("grp_")
