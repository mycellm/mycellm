"""Egress policy — the hard gate on where a prompt is allowed to go.

⚠️ THIS IS THE GAP THAT BLOCKED SWARM WORK, AND IT IS WHY IT IS BUILT FIRST.

Before this module, privacy scanning existed in exactly one place: the public
HTTP gateway (`api/gateway.py`), with an `X-Privacy-Override` header to bypass
it. `MycellmNode.route_inference` — the path everything else uses, including
the CLI, the OpenAI API and any fan-out — scanned nothing at all. So a swarm
coordinator calling `route_inference` N times would have shipped the prompt to
N public peers with zero scanning, while the single-model path through the
gateway blocked the same prompt.

`privacy.scan_with_policy` was already correct. It simply had one caller. That
is the same shape as every other defect found in this codebase: embedding
models tagged and never checked, `models_visible_to_network` written and never
called, `routing: "ensemble"` accepted and ignored.

The rule here: **a target's trust level is derived from where it actually is,
never from what the caller claims.** A caller may lower its own ceiling
(`trust: "local"`); it cannot raise a public peer's trust.
"""

from __future__ import annotations

from dataclasses import dataclass

from mycellm.execution.models import Target

#: Ordered weakest → strongest. A caller asking for `trust: "local"` must not
#: be routed to anything weaker than local.
TRUST_ORDER = {"untrusted": 0, "trusted": 1, "full": 2, "local": 3}


def trust_for(target: Target, own_networks: list[str] | None) -> str:
    """The trust level implied by sending a prompt to `target`.

    Derived from the target's actual location:

    - `local`     — this process. The prompt does not leave the machine.
    - `trusted`   — a peer or group inside a network we are a member of.
    - `untrusted` — anything else: the public network, or a peer whose
                    membership we cannot establish.

    A peer that declares no networks is treated as **untrusted**, not as
    "public and therefore fine". Routing treats an un-networked peer as
    reachable for availability; that is a different question from whether a
    credential may be sent to it.
    """
    if not target.is_remote:
        return "local"
    if own_networks and target.network_ids:
        if set(own_networks) & set(target.network_ids):
            return "trusted"
    return "untrusted"


@dataclass(frozen=True)
class EgressDecision:
    allowed: bool
    trust: str
    reason: str

    def __bool__(self) -> bool:
        return self.allowed


class EgressPolicy:
    """Decides, per target, whether this job's prompt may go there.

    Constructed once per job so the (relatively expensive) regex scan runs once
    rather than per candidate.
    """

    def __init__(
        self,
        prompt: str,
        requested_trust: str = "",
        own_networks: list[str] | None = None,
        override: bool = False,
    ):
        self._prompt = prompt
        self._requested = (requested_trust or "").strip().lower()
        self._own_networks = own_networks
        self._override = override
        self._cache: dict[str, dict] = {}

    def _scan(self, trust: str) -> dict:
        if trust not in self._cache:
            from mycellm.privacy import scan_with_policy
            self._cache[trust] = scan_with_policy(self._prompt, trust_level=trust)
        return self._cache[trust]

    def decide(self, target: Target) -> EgressDecision:
        trust = trust_for(target, self._own_networks)

        # 1. The caller's own ceiling. "local" means local; "trusted" excludes
        #    the public network. This is a floor the caller sets on itself.
        if self._requested in TRUST_ORDER:
            if TRUST_ORDER[trust] < TRUST_ORDER[self._requested]:
                return EgressDecision(
                    False, trust,
                    f"caller requires trust>={self._requested}, target is {trust}",
                )

        # 2. Local execution is always permitted — nothing leaves the machine,
        #    so there is nothing to scan for. Checked AFTER the caller ceiling
        #    so `trust: "local"` still behaves as a filter, not a bypass.
        if trust == "local":
            return EgressDecision(True, trust, "local execution")

        # 3. The sensitive-data gate. An override is honoured but reported, so
        #    it appears in the plan's reasons rather than vanishing.
        if self._override:
            return EgressDecision(True, trust, "privacy override acknowledged")

        result = self._scan(trust)
        if result["action"] == "block":
            labels = ", ".join(
                sorted({m.label for m in result["matches"] if m.severity == "high"})
            ) or result["highest_severity"]
            return EgressDecision(
                False, trust, f"sensitive data blocked for {trust} egress: {labels}"
            )
        if result["action"] == "warn":
            return EgressDecision(
                True, trust,
                f"allowed with warning ({result['highest_severity']} severity)",
            )
        return EgressDecision(True, trust, f"{trust} egress permitted")

    def blocked_everywhere(self, targets: list[Target]) -> bool:
        """True when no candidate passes — the caller must be told, not served.

        Silently degrading to a local model would be defensible; silently
        degrading to *nothing* and reporting success would not.
        """
        return bool(targets) and not any(self.decide(t).allowed for t in targets)
