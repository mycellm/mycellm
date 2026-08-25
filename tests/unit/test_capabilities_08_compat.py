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


class TestCrossImplementationGoldenVector:
    """Real CBOR bytes produced by the Swift (iOS) node, decoded here.

    ⚠️ EVERY OTHER TEST IN THIS FILE ONLY PROVES PYTHON AGREES WITH ITSELF.
    Both implementations were written from the same field list, so a shared
    misreading — a key spelled differently, a sub-map flattened, a bool encoded
    where a map was expected — would pass every round-trip test on each side
    and still fail on the wire, at which point the failure mode is a peer that
    is silently ineligible for work rather than an error anyone sees.

    This fixture is the byte stream `Capabilities.toCBORValue().encode()`
    produced on iOS. Its counterpart lives in the Swift suite
    (`Capabilities08CompatTests.pythonGoldenVector`), so the contract is
    pinned from both ends and neither side can drift alone.

    Regenerate with:
        xcodebuild ... -only-testing:MycellmTests/Capabilities08CompatTests/\
testEmitSwiftGoldenVector test | grep SWIFT_GOLDEN_VECTOR
    """

    SWIFT_GOLDEN_VECTOR = (
        "p2Ryb2xlZnNlZWRlcmZtb2RlbHOBrmRuYW1laXF3ZW4zLTM1YmR0YWdzgWRjaGF0ZHRpZXJl"
        "dGllcjJlcXVhbnRmUTRfS19NZXNjb3BlZnB1YmxpY2diYWNrZW5kY21seGdjdHhfbGVuGSAA"
        "aGZlYXR1cmVzgWlzdHJlYW1pbmdrcGFyYWxsZWxpc22iZHR5cGVoZXh0ZXJuYWxqd29ybGRf"
        "c2l6ZQRtZGVwbG95bWVudF9pZHRncnBfYXVyb3JhL3F3ZW4zLTM1Ym1wYXJhbV9jb3VudF9i"
        "+0BBgAAAAAAAb2V4ZWN1dGlvbl9yb2xlc4JocHJvcG9zZXJrc3ludGhlc2l6ZXJwc2Vydmlu"
        "Z19ncm91cF9pZGpncnBfYXVyb3JhcHRocm91Z2hwdXRfdG9rX3P7QEVAAAAAAABndmVyc2lv"
        "bmUwLjguMGhoYXJkd2FyZaljZ3B1Z0ExNyBQcm9lcG93ZXKha2NvbnN0cmFpbmVk9WZyYW1f"
        "Z2L7QCAAAAAAAABnYmFja2VuZGVtZXRhbGduZXR3b3JromlleHBlbnNpdmX1a2NvbnN0cmFp"
        "bmVk9Gd2cmFtX2di+0AgAAAAAAAAbGFyY2hpdGVjdHVyZWVhcm02NGxkZXZpY2VfY2xhc3Nm"
        "bW9iaWxlc2F2YWlsYWJsZV9tZW1vcnlfZ2L7QBAAAAAAAABpZXN0X3Rva19z+0ApAAAAAAAA"
        "a25ldHdvcmtfaWRzgWhuZXRfaG9tZW5tYXhfY29uY3VycmVudAI="
    )

    @staticmethod
    def _decode():
        import base64
        return Capabilities.from_dict(
            cbor2.loads(base64.b64decode(
                TestCrossImplementationGoldenVector.SWIFT_GOLDEN_VECTOR))
        )

    def test_decodes_real_cbor_produced_by_the_ios_node(self):
        caps = self._decode()
        assert caps.version == "0.8.0"
        assert caps.role == "seeder"
        assert caps.max_concurrent == 2
        assert caps.network_ids == ["net_home"]
        assert len(caps.models) == 1

    def test_ios_group_and_role_fields_survive_the_wire(self):
        m = self._decode().models[0]
        assert m.name == "qwen3-35b"
        assert m.serving_group_id == "grp_aurora"
        assert m.deployment_id == "grp_aurora/qwen3-35b"
        assert m.is_grouped is True
        assert m.execution_roles == ["proposer", "synthesizer"]
        assert m.can("proposer") is True
        # Declaring any role opts out of the ones not named, including direct.
        assert m.can("direct") is False

    def test_ios_parallelism_map_decodes_with_its_int_intact(self):
        # CBOR distinguishes int from float; Swift encodes `world_size` as an
        # unsigned int and a float here would mean the two encoders disagree.
        p = self._decode().models[0].parallelism
        assert p["type"] == "external"
        assert p["world_size"] == 4
        assert isinstance(p["world_size"], int)

    def test_ios_device_constraints_decode_from_the_nested_shape(self):
        h = self._decode().hardware
        assert h.architecture == "arm64"
        assert h.device_class == "mobile"
        assert h.ram_gb == 8.0
        assert h.available_memory_gb == 4.0
        assert h.power_constrained is True
        # Absent `thermal` block must read as False, not as missing/None.
        assert h.thermal_constrained is False
        assert h.network_expensive is True
        assert h.network_constrained is False
        assert h.is_constrained is True

    def test_a_07_python_decoder_would_also_accept_the_ios_payload(self):
        """The interop guarantee, from the other direction.

        A 0.7.1 node reads this same payload with a `from_dict` that knows
        none of the 0.8 keys. Simulated by dropping them: the remaining map
        must still decode to a usable model, which is what stops an iOS 0.8
        build from cutting itself off from the installed fleet.
        """
        import base64
        raw = cbor2.loads(base64.b64decode(self.SWIFT_GOLDEN_VECTOR))
        known_07 = {
            "name", "quant", "ctx_len", "backend", "tags", "tier",
            "param_count_b", "scope", "visible_networks", "features",
            "throughput_tok_s", "loaded_bytes",
        }
        model_07 = {k: v for k, v in raw["models"][0].items() if k in known_07}
        m = ModelCapability.from_dict(model_07)
        assert m.name == "qwen3-35b"
        assert m.backend == "mlx"
        assert m.param_count_b == 35.0
        # And the 0.7 hardware decoder sees the three keys it always saw.
        hw_07 = {k: v for k, v in raw["hardware"].items()
                 if k in {"gpu", "vram_gb", "backend"}}
        h = HardwareInfo.from_dict(hw_07)
        assert h.gpu == "A17 Pro"
        assert h.backend == "metal"
