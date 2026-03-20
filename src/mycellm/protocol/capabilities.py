"""Capability advertisement schema — signed by device key, exchanged over authenticated transport."""

from __future__ import annotations

from dataclasses import dataclass, field

import cbor2


@dataclass
class ModelCapability:
    """A model this node can serve."""

    name: str
    quant: str = ""
    ctx_len: int = 4096
    backend: str = "llama.cpp"
    tags: list[str] = field(default_factory=list)
    tier: str = ""
    param_count_b: float = 0.0
    scope: str = "home"  # "home" | "public" | "networks"
    visible_networks: list[str] = field(default_factory=list)  # network_ids when scope="networks"
    features: list[str] = field(default_factory=list)  # "streaming", "function_calling", "vision", "json_mode"
    throughput_tok_s: float = 0.0  # measured tokens/sec

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "quant": self.quant,
            "ctx_len": self.ctx_len,
            "backend": self.backend,
        }
        if self.tags:
            d["tags"] = self.tags
        if self.tier:
            d["tier"] = self.tier
        if self.param_count_b > 0:
            d["param_count_b"] = self.param_count_b
        if self.scope != "home":
            d["scope"] = self.scope
        if self.visible_networks:
            d["visible_networks"] = self.visible_networks
        if self.features:
            d["features"] = self.features
        if self.throughput_tok_s > 0:
            d["throughput_tok_s"] = self.throughput_tok_s
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ModelCapability:
        return cls(
            name=d["name"],
            quant=d.get("quant", ""),
            ctx_len=d.get("ctx_len", 4096),
            backend=d.get("backend", "llama.cpp"),
            tags=d.get("tags", []),
            tier=d.get("tier", ""),
            param_count_b=d.get("param_count_b", 0.0),
            scope=d.get("scope", "home"),
            visible_networks=d.get("visible_networks", []),
            features=d.get("features", []),
            throughput_tok_s=d.get("throughput_tok_s", 0.0),
        )


@dataclass
class HardwareInfo:
    """Hardware description for capability advertisement."""

    gpu: str = "none"
    vram_gb: float = 0.0
    backend: str = "cpu"

    def to_dict(self) -> dict:
        return {"gpu": self.gpu, "vram_gb": self.vram_gb, "backend": self.backend}

    @classmethod
    def from_dict(cls, d: dict) -> HardwareInfo:
        return cls(
            gpu=d.get("gpu", "none"),
            vram_gb=d.get("vram_gb", 0.0),
            backend=d.get("backend", "cpu"),
        )


@dataclass
class Capabilities:
    """Full capability advertisement for a node."""

    models: list[ModelCapability] = field(default_factory=list)
    hardware: HardwareInfo = field(default_factory=HardwareInfo)
    max_concurrent: int = 2
    est_tok_s: float = 0.0
    role: str = "seeder"
    version: str = "0.1.0"
    network_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "models": [m.to_dict() for m in self.models],
            "hardware": self.hardware.to_dict(),
            "max_concurrent": self.max_concurrent,
            "est_tok_s": self.est_tok_s,
            "role": self.role,
            "version": self.version,
        }
        if self.network_ids:
            d["network_ids"] = self.network_ids
        return d

    def to_cbor(self) -> bytes:
        return cbor2.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict) -> Capabilities:
        return cls(
            models=[ModelCapability.from_dict(m) for m in d.get("models", [])],
            hardware=HardwareInfo.from_dict(d.get("hardware", {})),
            max_concurrent=d.get("max_concurrent", 2),
            est_tok_s=d.get("est_tok_s", 0.0),
            role=d.get("role", "seeder"),
            version=d.get("version", "0.1.0"),
            network_ids=d.get("network_ids", []),
        )

    @classmethod
    def from_cbor(cls, data: bytes) -> Capabilities:
        return cls.from_dict(cbor2.loads(data))
