"""ModelResolver — maps user intent to concrete models across the network."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from mycellm.protocol.capabilities import ModelCapability
from mycellm.router.registry import PeerEntry, PeerRegistry

logger = logging.getLogger("mycellm.router")


# Tier thresholds based on estimated parameter count
TIER_THRESHOLDS = {
    "frontier": 65.0,  # >65B
    "capable": 13.0,   # >13B
    "fast": 3.0,       # >3B
    "tiny": 0.0,       # everything else
}

# Common parameter count patterns in model names
_PARAM_PATTERNS = [
    (r"(\d+)[xX](\d+)[bB]", lambda m: float(m.group(1)) * float(m.group(2))),  # MoE like 8x7B
    (r"(\d+\.?\d*)[bB]", lambda m: float(m.group(1))),  # Standard like 7B, 70B, 1.5B
    (r"(\d+\.?\d*)[mM]", lambda m: float(m.group(1)) / 1000),  # Millions like 350M
]


def estimate_param_count(model_name: str) -> float:
    """Estimate parameter count in billions from model name."""
    for pattern, extract in _PARAM_PATTERNS:
        match = re.search(pattern, model_name)
        if match:
            return extract(match)
    return 7.0  # default assumption


def derive_tier(param_count_b: float) -> str:
    """Derive model tier from parameter count."""
    for tier, threshold in TIER_THRESHOLDS.items():
        if param_count_b >= threshold:
            return tier
    return "tiny"


def derive_tags(model_name: str) -> list[str]:
    """Auto-derive tags from model name heuristics."""
    tags = ["chat"]  # all models are assumed chat-capable
    name_lower = model_name.lower()

    if any(k in name_lower for k in ("code", "coder", "starcoder", "deepseek-coder", "codellama")):
        tags.append("code")
    if any(k in name_lower for k in ("reason", "think", "r1", "qwq", "o1", "o3")):
        tags.append("reasoning")
    if any(k in name_lower for k in ("vision", "vl", "llava", "pixtral")):
        tags.append("vision")
    if any(k in name_lower for k in ("embed", "embedding")):
        tags = ["embedding"]  # override

    return tags


@dataclass
class ResolvedModel:
    """A resolved model with its source information."""
    model_name: str
    peer_id: str  # "" for local
    source: str  # "local", "quic", "fleet"
    score: float = 0.0
    tier: str = ""
    tags: list[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


class ModelResolver:
    """Resolves model requests to concrete models across the network."""

    def __init__(self, registry: PeerRegistry):
        self._registry = registry

    def resolve(
        self,
        requested: str,
        local_models: list[ModelCapability],
        fleet_registry: dict[str, dict] | None = None,
    ) -> list[ResolvedModel]:
        """Resolve a model request to a scored list of candidates.

        Handles:
        - Exact match: "llama-3.1-8b" -> that specific model
        - Tag match: "code" -> best code model on network
        - Tier match: "capable" -> best model in that tier
        - Empty/default: highest-tier model available

        Returns all candidates sorted by score (best first).
        """
        candidates: list[ResolvedModel] = []

        # Collect local models
        for m in local_models:
            param_b = getattr(m, 'param_count_b', 0.0) or estimate_param_count(m.name)
            tier = getattr(m, 'tier', '') or derive_tier(param_b)
            tags = getattr(m, 'tags', []) or derive_tags(m.name)
            candidates.append(ResolvedModel(
                model_name=m.name,
                peer_id="",
                source="local",
                tier=tier,
                tags=tags,
                score=self._score_model(param_b, tier, source="local"),
            ))

        # Collect QUIC peer models
        for entry in self._registry.all_peers():
            if entry.state.value not in ("routable", "serving", "authenticated"):
                continue
            for m in entry.capabilities.models:
                param_b = getattr(m, 'param_count_b', 0.0) or estimate_param_count(m.name)
                tier = getattr(m, 'tier', '') or derive_tier(param_b)
                tags = getattr(m, 'tags', []) or derive_tags(m.name)
                health = 1.0
                if entry.failure_count > 0:
                    health = 0.5 ** entry.failure_count
                candidates.append(ResolvedModel(
                    model_name=m.name,
                    peer_id=entry.peer_id,
                    source="quic",
                    tier=tier,
                    tags=tags,
                    score=self._score_model(param_b, tier, source="quic", health=health),
                ))

        # Collect fleet models
        if fleet_registry:
            for peer_id, entry in fleet_registry.items():
                if entry.get("status") != "approved":
                    continue
                caps = entry.get("capabilities", {})
                for m in caps.get("models", []):
                    name = m.get("name", m) if isinstance(m, dict) else m
                    param_b = estimate_param_count(name)
                    tier = derive_tier(param_b)
                    tags = derive_tags(name)
                    candidates.append(ResolvedModel(
                        model_name=name,
                        peer_id=entry.get("peer_id", ""),
                        source="fleet",
                        tier=tier,
                        tags=tags,
                        score=self._score_model(param_b, tier, source="fleet"),
                    ))

        if not candidates:
            return []

        # Filter by requested model
        if requested:
            filtered = self._filter_candidates(requested, candidates)
            if filtered:
                candidates = filtered

        # Sort by score (best first)
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    def _filter_candidates(
        self, requested: str, candidates: list[ResolvedModel]
    ) -> list[ResolvedModel]:
        """Filter candidates based on the requested string."""
        # Exact match
        exact = [c for c in candidates if c.model_name == requested]
        if exact:
            return exact

        # Tag match
        tag_match = [c for c in candidates if requested.lower() in [t.lower() for t in c.tags]]
        if tag_match:
            return tag_match

        # Tier match
        tier_match = [c for c in candidates if c.tier == requested.lower()]
        if tier_match:
            return tier_match

        # Substring match (fuzzy)
        substr = [c for c in candidates if requested.lower() in c.model_name.lower()]
        if substr:
            return substr

        return []

    def _score_model(
        self,
        param_b: float,
        tier: str,
        source: str = "local",
        health: float = 1.0,
    ) -> float:
        """Score a model candidate."""
        # Base score from tier
        tier_scores = {"frontier": 100, "capable": 60, "fast": 30, "tiny": 10}
        score = tier_scores.get(tier, 10)

        # Local preference bonus
        if source == "local":
            score *= 1.5
        elif source == "quic":
            score *= 1.2  # QUIC slightly preferred over fleet HTTP
        # fleet gets base score

        # Health factor
        score *= health

        return score
