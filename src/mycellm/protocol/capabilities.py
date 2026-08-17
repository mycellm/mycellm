"""Capability advertisement schema, exchanged over authenticated transport.

⚠️ CAPABILITIES ARE **NOT** SIGNED, despite what this docstring claimed until
0.8. `NodeHello.signable_data()` covers `nonce`, `timestamp` and `peer_id` only
(`node_hello.py`) — the capability payload sits outside the signature, and
`PEER_ANNOUNCE` updates carry no capability signature at all.

What that does and does not buy you:

- The QUIC+TLS session is authenticated, so a *third party* cannot forge or
  tamper with a peer's advertisement in flight.
- The peer itself can claim anything — models it lacks, throughput it cannot
  reach, hardware it does not have. Treat capabilities as a peer's own
  self-report, never as an attested fact, and never as an authorisation.

Fixing this properly means changing the signed byte range, which every 0.7.1
peer computes differently, so it cannot be done additively — it needs the
version field to become trustworthy first (see `Capabilities.version`). The
claim is corrected here rather than left standing, because a security property
that exists only in a docstring is worse than a documented gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cbor2


# Model tier boundaries (by parameter count in billions)
TIER_THRESHOLDS = [
    (8.0, 1),    # ≤8B = Tier 1
    (70.0, 2),   # ≤70B = Tier 2
    (float("inf"), 3),  # >70B = Tier 3
]


def normalize_model_name(name: str) -> str:
    """Strip transport-internal prefixes from a model name for display.

    The HTTP-relay backend prefixes models with `relay:` for routing
    disambiguation. Users shouldn't see that — model lists, stats,
    activity logs, and federation advertisements should all show the
    canonical model name.
    """
    if not name:
        return name
    if name.startswith("relay:"):
        return name[6:]
    return name

TIER_LABELS = {1: "tier1", 2: "tier2", 3: "tier3"}
TIER_NAMES = {1: "Standard (≤8B)", 2: "Large (≤70B)", 3: "Frontier (>70B)"}


def classify_tier(param_count_b: float) -> int:
    """Classify a model into a tier based on parameter count."""
    if param_count_b <= 0:
        return 1  # Unknown size defaults to Tier 1
    for threshold, tier in TIER_THRESHOLDS:
        if param_count_b <= threshold:
            return tier
    return 3


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
    loaded_bytes: int = 0  # approximate model footprint (file size on disk; 0 for remote)

    # ── 0.8 Adaptive Inference Fabric (additive, optional) ──────────────
    #
    # ⚠️ EVERY ONE OF THESE IS OMITTED FROM `to_dict()` WHEN UNSET, AND THAT
    # IS LOAD-BEARING, NOT TIDINESS. A 0.7.1 peer parses this map with
    # explicit `d.get(...)` lookups per key (see `from_dict` below), so it
    # ignores keys it does not know — but only if we never *require* them.
    # Emitting `parallelism: {"type": "standalone"}` on every model would
    # also inflate every announcement on a network where nothing uses it.
    #
    # Adding fields here is safe. Adding a new `MessageType` is NOT — but not
    # for the reason usually given. Both decoders reject an unknown type
    # (Python raises `ValueError` from `MessageType(obj["type"])`,
    # `envelope.py`; iOS throws `invalidCBOR`, `MessageEnvelope.swift:73`),
    # and both transports then swallow it: `quic.py:118-121` logs and returns,
    # `QUICTransport.swift:155-158` uses `try?` and returns. The connection
    # SURVIVES; the message is silently lost. That is worse than a hard
    # failure for request/response — a `send_and_wait` on a new type hangs
    # until timeout instead of failing fast — and it cannot be feature-gated,
    # because the advertised capability version is meaningful only as of the
    # fix in this release. So 0.8 information travels inside existing
    # messages, in these fields, rather than in new message types.

    #: Which deployment serves this model. Empty = served by this peer directly.
    deployment_id: str = ""
    #: The serving group the deployment belongs to (e.g. an oMLX cluster).
    serving_group_id: str = ""
    #: {"type": "standalone"|"tensor"|"pipeline"|"external", "world_size": int}
    parallelism: dict = field(default_factory=dict)

    #: What this model may be asked to do in a multi-stage job:
    #: "direct", "proposer", "critic", "synthesizer", "verifier", "embed".
    #: Empty means "direct only" — 0.7 semantics.
    #:
    #: This field was written, DELETED for having no consumer, and restored in
    #: the same release once the execution planner existed to read it. That
    #: sequence is the point: an advertised capability nothing enforces is the
    #: bug this codebase keeps shipping (embedding models tagged
    #: `["embedding"]` while the chat path ignored the tag; `routing:
    #: "ensemble"` accepted by the public API and implemented nowhere).
    #: `ExecutionPlanner._eligible_for` is the reader — if you add a role,
    #: teach it there in the same change.
    execution_roles: list[str] = field(default_factory=list)

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
        if self.loaded_bytes > 0:
            d["loaded_bytes"] = self.loaded_bytes
        # 0.8 fields — emitted only when set, so a 0.7 network sees the
        # byte-identical announcement it saw before.
        if self.deployment_id:
            d["deployment_id"] = self.deployment_id
        if self.serving_group_id:
            d["serving_group_id"] = self.serving_group_id
        if self.execution_roles:
            d["execution_roles"] = self.execution_roles
        if self.parallelism:
            d["parallelism"] = self.parallelism
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
            loaded_bytes=d.get("loaded_bytes", 0),
            deployment_id=d.get("deployment_id", ""),
            serving_group_id=d.get("serving_group_id", ""),
            execution_roles=d.get("execution_roles", []),
            parallelism=d.get("parallelism", {}),
        )

    def can(self, role: str) -> bool:
        """True if this model may be used for `role`.

        Empty `execution_roles` means 0.7 semantics: direct serving only. Use
        this rather than re-deriving the rule at each call site — the embedding
        bug happened because two places disagreed about what a tag meant.
        """
        if not self.execution_roles:
            return role == "direct"
        return role in self.execution_roles

    @property
    def is_grouped(self) -> bool:
        """True if a ServingGroup serves this, not this peer's own backend."""
        return bool(self.serving_group_id)


@dataclass
class HardwareInfo:
    """Hardware description for capability advertisement."""

    gpu: str = "none"
    vram_gb: float = 0.0
    backend: str = "cpu"

    # ── 0.8 additive telemetry ──────────────────────────────────────────
    #
    # A scheduler that sees only gpu/vram routes to a phone that is at 5% on
    # battery and thermally throttled. iOS already computes all of this for
    # its own `/v1/node/status` `device` block and already demotes itself to
    # `consumer` — these fields let the *rest of the fleet* see the same
    # facts instead of each node discovering them by failing a request.
    ram_gb: float = 0.0
    available_memory_gb: float = 0.0
    architecture: str = ""       # "arm64", "x86_64"
    device_class: str = ""       # "server" | "desktop" | "laptop" | "mobile"
    #: True when the device is power-limited (Low Power Mode / low battery).
    power_constrained: bool = False
    #: True when the device is thermally throttled.
    thermal_constrained: bool = False
    #: True when the network costs money (cellular).
    network_expensive: bool = False
    #: True when the user asked the system to go easy (Low Data Mode).
    network_constrained: bool = False

    def to_dict(self) -> dict:
        d = {"gpu": self.gpu, "vram_gb": self.vram_gb, "backend": self.backend}
        # Same rule as ModelCapability: absent unless set, so a 0.7 network
        # sees an unchanged payload.
        if self.ram_gb > 0:
            d["ram_gb"] = self.ram_gb
        if self.available_memory_gb > 0:
            d["available_memory_gb"] = self.available_memory_gb
        if self.architecture:
            d["architecture"] = self.architecture
        if self.device_class:
            d["device_class"] = self.device_class
        if self.power_constrained:
            d["power"] = {"constrained": True}
        if self.thermal_constrained:
            d["thermal"] = {"constrained": True}
        if self.network_expensive or self.network_constrained:
            d["network"] = {
                "expensive": self.network_expensive,
                "constrained": self.network_constrained,
            }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> HardwareInfo:
        power = d.get("power") or {}
        thermal = d.get("thermal") or {}
        network = d.get("network") or {}
        return cls(
            gpu=d.get("gpu", "none"),
            vram_gb=d.get("vram_gb", 0.0),
            backend=d.get("backend", "cpu"),
            ram_gb=d.get("ram_gb", 0.0),
            available_memory_gb=d.get("available_memory_gb", 0.0),
            architecture=d.get("architecture", ""),
            device_class=d.get("device_class", ""),
            power_constrained=bool(power.get("constrained", False)),
            thermal_constrained=bool(thermal.get("constrained", False)),
            network_expensive=bool(network.get("expensive", False)),
            network_constrained=bool(network.get("constrained", False)),
        )

    @property
    def is_constrained(self) -> bool:
        """True when this device should not be handed discretionary work.

        Mirrors the demotion rule iOS already applies to itself, so a
        scheduler and the device agree about when it is in no state to serve.
        """
        return self.power_constrained or self.thermal_constrained


@dataclass
class Capabilities:
    """Full capability advertisement for a node."""

    models: list[ModelCapability] = field(default_factory=list)
    hardware: HardwareInfo = field(default_factory=HardwareInfo)
    max_concurrent: int = 2
    est_tok_s: float = 0.0
    role: str = "seeder"
    #: The advertising node's mycellm version.
    #:
    #: ⚠️ THIS WAS HARDCODED "0.1.0" ON EVERY PYTHON NODE UNTIL 0.8, WHILE iOS
    #: CORRECTLY SENT ITS REAL VERSION. Nothing consumed it, so nothing broke
    #: — but it meant the field could not be used to gate anything, because a
    #: peer claiming "0.1.0" might be any release ever shipped. That blocks
    #: the only safe way to introduce a new `MessageType`: send it solely to
    #: peers known new enough to decode it, since an unknown type is silently
    #: dropped rather than refused (see the note on ModelCapability).
    #:
    #: Feature-gating on this becomes possible only once truthful versions
    #: have been in the wild long enough that "0.1.0" is rare. The clock
    #: starts here.
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
