"""Routing options must be enforced or refused — never silently ignored.

⚠️ REGRESSION. The public API accepted a `mycellm` routing block whose
options did nothing:

  routing      consulted NOWHERE — not "best", not "fastest", not
               "ensemble". A client asking for an ensemble got ordinary
               single-model routing and HTTP 200.
  min_context  reached a branch whose body is literally `pass`.
  max_cost     carried into QualityConstraints and never read, so a caller
               who set a credit ceiling had none.

Silently ignoring a constraint is worse than refusing it: the caller
believes a limit is in force and has no way to discover otherwise.
"""

import pytest

from mycellm.api.openai import (
    SUPPORTED_ROUTING,
    MycellmRouting,
    unsupported_routing_options,
)


class TestUnsupportedRoutingStrategies:
    def test_ensemble_is_refused(self):
        problems = unsupported_routing_options(MycellmRouting(routing="ensemble"))
        assert problems, "ensemble is not implemented and must not be accepted"
        assert "ensemble" in problems[0]

    def test_fastest_is_refused(self):
        problems = unsupported_routing_options(MycellmRouting(routing="fastest"))
        assert problems
        assert "fastest" in problems[0]

    def test_the_message_names_what_is_supported(self):
        (msg,) = unsupported_routing_options(MycellmRouting(routing="ensemble"))
        for supported in SUPPORTED_ROUTING:
            assert supported in msg, "a refusal must say what IS available"

    def test_best_is_accepted(self):
        assert unsupported_routing_options(MycellmRouting(routing="best")) == []

    def test_default_block_is_accepted(self):
        # The default routing value must not make every request an error.
        assert unsupported_routing_options(MycellmRouting()) == []

    def test_absent_block_is_accepted(self):
        assert unsupported_routing_options(None) == []


class TestUnenforcedConstraints:
    def test_max_cost_is_refused(self):
        problems = unsupported_routing_options(MycellmRouting(max_cost=5.0))
        assert problems, "a credit ceiling that is not applied must not report success"
        assert "max_cost" in problems[0]

    def test_min_context_is_refused(self):
        problems = unsupported_routing_options(MycellmRouting(min_context=32768))
        assert problems
        assert "min_context" in problems[0]

    @pytest.mark.parametrize("value", [0, 0.0])
    def test_zero_means_unset_and_is_fine(self, value):
        assert unsupported_routing_options(
            MycellmRouting(max_cost=value, min_context=int(value))) == []

    def test_every_problem_is_reported_not_just_the_first(self):
        problems = unsupported_routing_options(
            MycellmRouting(routing="ensemble", max_cost=5.0, min_context=32768))
        assert len(problems) == 3, f"expected all three, got {problems}"


class TestConstraintsThatDoWork:
    """These are enforced in `_apply_quality_filter`, so they must NOT refuse."""

    def test_min_tier_is_accepted(self):
        assert unsupported_routing_options(MycellmRouting(min_tier="frontier")) == []

    def test_min_params_is_accepted(self):
        assert unsupported_routing_options(MycellmRouting(min_params=7.0)) == []

    def test_required_tags_are_accepted(self):
        assert unsupported_routing_options(MycellmRouting(required_tags=["code"])) == []

    def test_trust_is_accepted(self):
        assert unsupported_routing_options(MycellmRouting(trust="local")) == []


class TestGuardPlacement:
    """The guard must sit above the stream branch.

    The streaming path never read `body.mycellm` at all, so putting the check
    with the non-streaming constraint handling would have exempted every
    streaming request — the original bug surviving its own fix.
    """

    def test_guard_runs_before_the_stream_branch(self):
        import inspect

        from mycellm.api.openai import chat_completions
        src = inspect.getsource(chat_completions)
        guard_at = src.index("unsupported_routing_options(body.mycellm)")
        stream_at = src.index("if body.stream")
        assert guard_at < stream_at, \
            "the guard must run before the request branches into streaming"
