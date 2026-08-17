"""Wire-compatibility tests for the 0.8 additive capability schema.

§20.1 makes 0.7.x interop release-blocking. These tests encode what
"additive" actually has to mean on this wire, in both directions:

  new node → old peer    old peer must ignore fields it never heard of
  old node → new peer    new peer must not require fields nobody sent

The 0.7.1 decoder is simulated by decoding with only the keys 0.7.1 knew
about — which is exactly what its `from_dict` does, since it reads named
keys and ignores the rest.
"""

import cbor2
import pytest

from mycellm.protocol.capabilities import (
    Capabilities,
    HardwareInfo,
    ModelCapability,
)

# The exact key set 0.7.1's ModelCapability.from_dict consumed.
V07_MODEL_KEYS = {
    "name", "quant", "ctx_len", "backend", "tags", "tier", "param_count_b",
    "scope", "visible_networks", "features", "throughput_tok_s", "loaded_bytes",
}
V07_HARDWARE_KEYS = {"gpu", "vram_gb", "backend"}


def decode_as_v07_model(d: dict) -> dict:
    """What a 0.7.1 peer actually retains from a 0.8 announcement."""
    return {k: v for k, v in d.items() if k in V07_MODEL_KEYS}


class TestAnnouncementsAreUnchangedWhenUnused:
    """A 0.8 node on a 0.7 network must emit byte-identical announcements.

    Not merely "compatible" — identical. If 0.8 nodes started emitting
    `parallelism: {"type": "standalone"}` on every model, every announcement
    on every network would grow for information nothing consumes.
    """

    def test_model_dict_has_no_new_keys_when_unset(self):
        m = ModelCapability(name="qwen3", ctx_len=8192)
        assert set(m.to_dict()) <= V07_MODEL_KEYS

    def test_hardware_dict_has_no_new_keys_when_unset(self):
        h = HardwareInfo(gpu="M1", vram_gb=16.0, backend="metal")
        assert set(h.to_dict()) == V07_HARDWARE_KEYS

    def test_cbor_bytes_identical_to_a_07_shaped_payload(self):
        m = ModelCapability(name="qwen3", quant="Q4_K_M", ctx_len=8192,
                            backend="llama.cpp", tags=["chat"])
        expected = {"name": "qwen3", "quant": "Q4_K_M", "ctx_len": 8192,
                    "backend": "llama.cpp", "tags": ["chat"]}
        assert cbor2.dumps(m.to_dict(), canonical=True) == \
               cbor2.dumps(expected, canonical=True)


class TestOldPeerSurvivesNewFields:
    def test_07_peer_ignores_every_08_field(self):
        m = ModelCapability(
            name="qwen3",
            deployment_id="dep_abc",
            serving_group_id="grp_omlx",
            parallelism={"type": "tensor", "world_size": 2},
        )
        kept = decode_as_v07_model(m.to_dict())
        # It still learns the model exists and how to route to it.
        assert kept["name"] == "qwen3"
        # And it retains nothing it cannot interpret.
        assert "deployment_id" not in kept
        assert "parallelism" not in kept

    def test_07_peer_can_still_reconstruct_a_usable_capability(self):
        m = ModelCapability(name="qwen3", ctx_len=8192, backend="mlx",
                            deployment_id="dep_abc",
                            parallelism={"type": "tensor", "world_size": 2})
        rebuilt = ModelCapability.from_dict(decode_as_v07_model(m.to_dict()))
        assert (rebuilt.name, rebuilt.ctx_len, rebuilt.backend) == \
               ("qwen3", 8192, "mlx")

    def test_hardware_nested_blocks_are_ignorable(self):
        h = HardwareInfo(gpu="A17", vram_gb=8.0, backend="metal",
                         power_constrained=True, thermal_constrained=True,
                         network_expensive=True)
        kept = {k: v for k, v in h.to_dict().items() if k in V07_HARDWARE_KEYS}
        assert kept == {"gpu": "A17", "vram_gb": 8.0, "backend": "metal"}


class TestNewPeerSurvivesOldAnnouncements:
    def test_08_decodes_a_bare_07_model(self):
        m = ModelCapability.from_dict(
            {"name": "llama3", "quant": "Q4_K_M", "ctx_len": 4096,
             "backend": "llama.cpp"})
        assert m.name == "llama3"
        assert m.deployment_id == ""
        assert m.serving_group_id == ""
        assert m.parallelism == {}
        assert m.is_grouped is False

    def test_08_decodes_a_bare_07_hardware_block(self):
        h = HardwareInfo.from_dict({"gpu": "3090", "vram_gb": 24.0, "backend": "cuda"})
        assert h.ram_gb == 0.0
        assert h.is_constrained is False

    def test_minimum_viable_payload_still_decodes(self):
        assert ModelCapability.from_dict({"name": "m"}).name == "m"
        assert HardwareInfo.from_dict({}).gpu == "none"


class TestRoundTrip:
    def test_full_08_capability_round_trips(self):
        m = ModelCapability(
            name="qwen3-27b", quant="Q4_K_M", ctx_len=32768, backend="mlx",
            deployment_id="dep_1", serving_group_id="grp_1",
            parallelism={"type": "tensor", "world_size": 2},
        )
        back = ModelCapability.from_dict(cbor2.loads(cbor2.dumps(m.to_dict())))
        assert back == m

    def test_full_08_hardware_round_trips(self):
        h = HardwareInfo(gpu="M1", vram_gb=16.0, backend="metal", ram_gb=16.0,
                         available_memory_gb=7.5, architecture="arm64",
                         device_class="mobile", power_constrained=True,
                         thermal_constrained=False, network_expensive=True,
                         network_constrained=True)
        assert HardwareInfo.from_dict(cbor2.loads(cbor2.dumps(h.to_dict()))) == h


class TestConstraintReporting:
    @pytest.mark.parametrize("kwargs,expected", [
        ({}, False),
        ({"power_constrained": True}, True),
        ({"thermal_constrained": True}, True),
        ({"network_expensive": True}, False),   # costs money, still capable
    ])
    def test_is_constrained(self, kwargs, expected):
        assert HardwareInfo(**kwargs).is_constrained is expected

    def test_expensive_and_constrained_stay_distinct(self):
        # They were OR'd into one flag on iOS once; the right response differs.
        h = HardwareInfo.from_dict({"network": {"expensive": True, "constrained": False}})
        assert h.network_expensive is True
        assert h.network_constrained is False


class TestFullCapabilitiesEnvelope:
    def test_capabilities_round_trip_with_08_models(self):
        caps = Capabilities(
            models=[ModelCapability(name="a", serving_group_id="g1"),
                    ModelCapability(name="b")],
            hardware=HardwareInfo(gpu="M1", device_class="desktop"),
        )
        back = Capabilities.from_dict(cbor2.loads(cbor2.dumps(caps.to_dict())))
        assert [m.name for m in back.models] == ["a", "b"]
        assert back.models[0].is_grouped is True
        assert back.models[1].is_grouped is False
        assert back.hardware.device_class == "desktop"
