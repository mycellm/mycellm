"""Egress policy — the hard gate on where a prompt may go.

⚠️ THIS IS THE GAP THAT BLOCKED SWARM WORK. Before this module, privacy
scanning lived in exactly one place: the public HTTP gateway
(`api/gateway.py`), with a header to bypass it. `route_inference` — the path
the CLI, the OpenAI API and any fan-out use — scanned nothing. A swarm
coordinator would therefore have shipped a prompt to N public peers with zero
scanning, while the same prompt through the gateway was blocked.

`privacy.scan_with_policy` was already correct. It had one caller.
"""

import pytest

from mycellm.execution.models import Target
from mycellm.execution.policy import EgressPolicy, trust_for

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
SECRET_PROMPT = f"deploy with key {AWS_KEY} please"
BENIGN = "what is the capital of France"

LOCAL = Target(model="m", kind="local")
PUBLIC_PEER = Target(model="m", kind="peer", peer_id="p" * 32)
NET_PEER = Target(model="m", kind="peer", peer_id="q" * 32, network_ids=("net-A",))
GROUP = Target(model="m", kind="group", serving_group_id="grp_omlx")


class TestTrustDerivation:
    """Trust comes from where the target IS, never from what a caller says."""

    def test_local_is_local(self):
        assert trust_for(LOCAL, ["net-A"]) == "local"

    def test_peer_sharing_a_network_is_trusted(self):
        assert trust_for(NET_PEER, ["net-A"]) == "trusted"

    def test_peer_with_no_shared_network_is_untrusted(self):
        assert trust_for(NET_PEER, ["net-B"]) == "untrusted"

    def test_peer_declaring_no_networks_is_untrusted(self):
        # Routing treats an un-networked peer as reachable for availability.
        # That is a different question from whether a credential may go there.
        assert trust_for(PUBLIC_PEER, ["net-A"]) == "untrusted"

    def test_group_is_remote_even_though_we_execute_it(self):
        # A relay-fronted group runs through THIS process, but the prompt still
        # leaves the machine over HTTP. Calling it local would be the exact
        # kind of quiet mistake this module exists to prevent.
        assert GROUP.is_remote is True
        assert trust_for(GROUP, ["net-A"]) == "untrusted"


class TestTheGateBlocks:
    def test_credential_is_blocked_from_a_public_peer(self):
        p = EgressPolicy(SECRET_PROMPT)
        d = p.decide(PUBLIC_PEER)
        assert not d.allowed
        assert "sensitive" in d.reason.lower()
        assert "AWS" in d.reason, "the refusal must name what it found"

    def test_credential_is_still_allowed_locally(self):
        # Nothing leaves the machine, so there is nothing to protect against.
        assert EgressPolicy(SECRET_PROMPT).decide(LOCAL).allowed

    def test_benign_prompt_goes_anywhere(self):
        p = EgressPolicy(BENIGN)
        assert p.decide(LOCAL).allowed
        assert p.decide(PUBLIC_PEER).allowed
        assert p.decide(GROUP).allowed

    def test_credential_is_blocked_from_a_group_too(self):
        # The group path is the one a swarm is most likely to use.
        assert not EgressPolicy(SECRET_PROMPT).decide(GROUP).allowed

    def test_system_prompts_are_scanned(self):
        """Credentials get pasted into system prompts, not just user turns."""
        from mycellm.execution.models import Job
        job = Job(job_id="j", model="m", messages=[
            {"role": "system", "content": f"api key: {AWS_KEY}"},
            {"role": "user", "content": "hello"},
        ])
        assert AWS_KEY in job.prompt_text()
        assert not EgressPolicy(job.prompt_text()).decide(PUBLIC_PEER).allowed

    def test_multimodal_content_parts_are_scanned(self):
        from mycellm.execution.models import Job
        job = Job(job_id="j", model="m", messages=[
            {"role": "user", "content": [{"type": "text", "text": f"key {AWS_KEY}"}]},
        ])
        assert AWS_KEY in job.prompt_text()


class TestCallerCeiling:
    """A caller may lower its own ceiling; it cannot raise a peer's trust."""

    def test_trust_local_excludes_every_remote_target(self):
        p = EgressPolicy(BENIGN, requested_trust="local")
        assert p.decide(LOCAL).allowed
        assert not p.decide(NET_PEER).allowed
        assert not p.decide(PUBLIC_PEER).allowed
        assert not p.decide(GROUP).allowed

    def test_trust_trusted_excludes_the_public_network(self):
        p = EgressPolicy(BENIGN, requested_trust="trusted", own_networks=["net-A"])
        assert p.decide(NET_PEER).allowed
        assert not p.decide(PUBLIC_PEER).allowed

    def test_the_ceiling_applies_before_the_local_shortcut(self):
        # `trust: local` must behave as a filter, not become a bypass that
        # lets everything through because local is always fine.
        p = EgressPolicy(SECRET_PROMPT, requested_trust="local")
        assert p.decide(LOCAL).allowed
        assert not p.decide(PUBLIC_PEER).allowed

    def test_an_unknown_trust_value_is_not_a_ceiling(self):
        p = EgressPolicy(BENIGN, requested_trust="banana")
        assert p.decide(PUBLIC_PEER).allowed


class TestOverride:
    def test_override_allows_but_reports(self):
        p = EgressPolicy(SECRET_PROMPT, override=True)
        d = p.decide(PUBLIC_PEER)
        assert d.allowed
        assert "override" in d.reason, "an override must be visible, not silent"

    def test_override_does_not_defeat_the_caller_ceiling(self):
        # The override is about sensitive-data detection, not about ignoring
        # an explicit "keep this local".
        p = EgressPolicy(SECRET_PROMPT, requested_trust="local", override=True)
        assert not p.decide(PUBLIC_PEER).allowed


class TestBlockedEverywhere:
    def test_reports_when_nothing_is_eligible(self):
        p = EgressPolicy(SECRET_PROMPT)
        assert p.blocked_everywhere([PUBLIC_PEER, GROUP]) is True

    def test_false_when_something_is_eligible(self):
        p = EgressPolicy(SECRET_PROMPT)
        assert p.blocked_everywhere([PUBLIC_PEER, LOCAL]) is False

    def test_empty_candidate_list_is_not_blocked(self):
        # "nothing available" and "everything refused" are different problems.
        assert EgressPolicy(SECRET_PROMPT).blocked_everywhere([]) is False


class TestScanIsCachedPerTrustLevel:
    def test_repeated_decisions_scan_once(self, monkeypatch):
        calls = []
        import mycellm.privacy as privacy
        real = privacy.scan_with_policy

        def counting(text, trust_level="untrusted"):
            calls.append(trust_level)
            return real(text, trust_level=trust_level)

        monkeypatch.setattr(privacy, "scan_with_policy", counting)
        p = EgressPolicy(BENIGN)
        for _ in range(5):
            p.decide(PUBLIC_PEER)
        assert len(calls) == 1, f"regex scan should run once per trust level, ran {len(calls)}"


class TestSeverityTiers:
    """The gate inherits `scan_with_policy`'s existing tiers; it does not invent
    its own. high → block, medium → allow-with-warning, low/none → allow.

    A medium match being *allowed* is deliberate, pre-existing policy — the
    first version of this test assumed a password phrase would be blocked,
    which was an assumption about the code rather than a reading of it.
    Encoding the guess would have made the suite agree with a fiction.
    """

    @pytest.mark.parametrize("prompt,blocked,warned", [
        (f"aws key {AWS_KEY}", True, False),                       # high
        ("-----BEGIN RSA PRIVATE KEY-----", True, False),           # high
        ("here is my password: hunter2please", False, True),        # medium
        ("mail me at a@b.com", False, True),                        # medium (PII)
        ("what is 2+2", False, False),
        ("", False, False),
    ])
    def test_tiers(self, prompt, blocked, warned):
        d = EgressPolicy(prompt).decide(PUBLIC_PEER)
        assert (not d.allowed) is blocked, f"{prompt[:40]!r}: allowed={d.allowed}"
        assert ("warning" in d.reason) is warned, f"{prompt[:40]!r}: {d.reason}"

    def test_a_warned_prompt_surfaces_in_plan_reasons(self):
        """A warning nobody sees is the same as no warning."""
        from mycellm.execution.models import Job
        from mycellm.execution.planner import ExecutionPlanner
        job = Job(job_id="j", model="m",
                  messages=[{"role": "user", "content": "my password is hunter2please"}])
        plan = ExecutionPlanner().plan(job, [PUBLIC_PEER])
        assert any("warning" in r for r in plan.reasons), plan.reasons
