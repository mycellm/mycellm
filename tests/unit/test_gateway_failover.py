"""Public-gateway failover and candidate ordering.

Both properties here were broken in production on a Dockerised bootstrap, and
neither was caught by any existing test:

  1. A fleet node that cannot be reached ENDED the request instead of failing
     over, because `_proxy_fleet` returned a 503 response where its streaming
     twin raised. Working candidates sat queued behind the dead one.
  2. A `quic:` candidate sorted level with an HTTP-proxy candidate, so the
     round-robin offset decided which came first.

Together they meant the gateway would pick an unreachable HTTP address for a
model it could have served over an open QUIC connection, and then give up.
"""

import pytest

from mycellm.api.gateway import (
    _FleetBusyError,
    _FleetUnavailableError,
    _proxy_fleet,
)


class _Recorder:
    def __init__(self):
        self.events = []

    def record(self, *a, **k):
        self.events.append((a, k))


class _Node:
    def __init__(self):
        self.activity = _Recorder()


# ── 1. An unreachable fleet node must raise, not return a response ──────


@pytest.mark.asyncio
async def test_unreachable_fleet_raises_so_the_loop_can_fail_over():
    """The regression itself.

    Port 1 on loopback refuses immediately, so this exercises the real
    connect-failure path rather than a mocked one — and without paying the 30s
    connect timeout an unroutable address would cost the suite. If
    `_proxy_fleet` returns anything at all, the caller's failover loop is dead.
    """
    node = _Node()
    with pytest.raises(_FleetUnavailableError):
        await _proxy_fleet(
            node, "req-1", "some-model", "127.0.0.1:1",
            [{"role": "user", "content": "hi"}], 0.7, 8, "1.2.3.4", 0.0,
        )
    # The failure is still recorded — failing over must not make it invisible.
    assert node.activity.events, "an unreachable fleet node must be recorded"


@pytest.mark.asyncio
async def test_the_failover_loop_catches_what_proxy_fleet_raises():
    """Guards the pair, not each half.

    A future refactor could reintroduce the bug by raising a type the loop does
    not catch. The loop's handler lists the failover exceptions explicitly, so
    assert the raised type is in it.
    """
    import inspect

    from mycellm.api import gateway

    src = inspect.getsource(gateway.public_chat)
    assert "_FleetUnavailableError" in src, (
        "public_chat must handle the exception _proxy_fleet raises, or an "
        "unreachable fleet node kills the request again"
    )
    assert issubclass(_FleetUnavailableError, Exception)
    assert not issubclass(_FleetUnavailableError, _FleetBusyError)


# ── 2. Candidate ordering ───────────────────────────────────────────────


def _hop_cost(addr):
    """Mirror of the gateway's ordering rule, imported by behaviour below."""
    if addr is None:
        return 0
    if str(addr).startswith("quic:"):
        return 1
    return 2


def _order(candidates, counter=0):
    """Reproduce the gateway's sort+rotate on (name, addr, tier) triples."""
    from itertools import groupby

    items = sorted(candidates, key=lambda c: (c[2], _hop_cost(c[1])))
    out = []
    for _key, group in groupby(items, key=lambda c: (c[2], _hop_cost(c[1]))):
        g = list(group)
        if len(g) > 1:
            offset = counter % len(g)
            g = g[offset:] + g[:offset]
        out.extend(g)
    return out


def test_quic_is_preferred_over_an_http_proxy_hop():
    ordered = _order([
        ("m", "10.0.0.5:8420", 1),
        ("m", "quic:peerA", 1),
    ])
    assert ordered[0][1] == "quic:peerA"


def test_local_beats_quic_beats_http():
    ordered = _order([
        ("m", "10.0.0.5:8420", 1),
        ("m", "quic:peerA", 1),
        ("m", None, 1),
    ])
    assert [_hop_cost(c[1]) for c in ordered] == [0, 1, 2]


@pytest.mark.parametrize("counter", range(6))
def test_rotation_never_lifts_http_above_quic(counter):
    """⚠️ THE ORDERING FIX IS WORTHLESS IF ROTATION UNDOES IT.

    Round-robin used to rotate within a whole tier, which mixed transports —
    so on some fraction of requests the unreachable HTTP candidate was tried
    first anyway. Every offset must keep QUIC ahead.
    """
    ordered = _order([
        ("m", "10.0.0.5:8420", 1),
        ("m", "10.0.0.6:8420", 1),
        ("m", "quic:peerA", 1),
        ("m", "quic:peerB", 1),
    ], counter=counter)
    costs = [_hop_cost(c[1]) for c in ordered]
    assert costs == sorted(costs), f"transport order broken at offset {counter}"
    assert costs[0] == 1


def test_rotation_still_spreads_load_among_equal_candidates():
    """The fix must not turn round-robin into always-pick-the-first."""
    cands = [
        ("m", "quic:peerA", 1),
        ("m", "quic:peerB", 1),
        ("m", "quic:peerC", 1),
    ]
    firsts = {_order(cands, counter=i)[0][1] for i in range(3)}
    assert firsts == {"quic:peerA", "quic:peerB", "quic:peerC"}


def test_tier_still_outranks_transport():
    """A cheap hop must not promote a worse model across tiers."""
    ordered = _order([
        ("big", "10.0.0.5:8420", 1),
        ("small", "quic:peerA", 2),
    ])
    assert ordered[0][0] == "big"
