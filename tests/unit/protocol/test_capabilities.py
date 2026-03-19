"""Tests for capability advertisement schema."""

from mycellm.protocol.capabilities import (
    Capabilities,
    HardwareInfo,
    ModelCapability,
)


def test_model_capability_roundtrip():
    m = ModelCapability(name="qwen-7b", quant="Q4_K_M", ctx_len=8192)
    d = m.to_dict()
    loaded = ModelCapability.from_dict(d)
    assert loaded.name == "qwen-7b"
    assert loaded.quant == "Q4_K_M"
    assert loaded.ctx_len == 8192


def test_capabilities_cbor_roundtrip():
    caps = Capabilities(
        models=[
            ModelCapability(name="llama-7b", quant="Q4_K_M"),
            ModelCapability(name="qwen-7b", quant="Q5_K_S", ctx_len=16384),
        ],
        hardware=HardwareInfo(gpu="RTX 4090", vram_gb=24.0, backend="cuda"),
        max_concurrent=4,
        est_tok_s=62.0,
        role="seeder",
    )
    data = caps.to_cbor()
    loaded = Capabilities.from_cbor(data)

    assert len(loaded.models) == 2
    assert loaded.models[0].name == "llama-7b"
    assert loaded.hardware.gpu == "RTX 4090"
    assert loaded.hardware.vram_gb == 24.0
    assert loaded.est_tok_s == 62.0


def test_capabilities_defaults():
    caps = Capabilities()
    assert caps.models == []
    assert caps.hardware.gpu == "none"
    assert caps.role == "seeder"
    assert caps.version == "0.1.0"
