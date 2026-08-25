"""Resolving the caller's real address behind a reverse proxy.

Every per-IP limit in mycellm keys on this: public chat's anon budget, the
announce limiter, the auth lockout. On the public bootstrap they were all one
shared bucket, because prime runs behind Caddy in Docker and every caller on
earth arrived as the bridge gateway — confirmed live, with every registered
node stamped `192.168.80.1`.

The fix reads `X-Forwarded-For`, which is caller-supplied and therefore a
spoofing surface: trusted blindly, it turns every rate limit into a suggestion,
since an attacker can present a fresh address per request. So half of these
tests are adversarial — they exist to prove the header is ignored whenever we
cannot vouch for the hop that set it.
"""

from mycellm.api.client_ip import (
    DEFAULT_TRUSTED_PROXIES,
    client_address,
    parse_trusted,
)


class FakeRequest:
    def __init__(self, peer: str | None, headers: dict | None = None):
        self.client = _Peer(peer) if peer else None
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


class _Peer:
    def __init__(self, host):
        self.host = host


def req(peer, xff=None):
    return FakeRequest(peer, {"X-Forwarded-For": xff} if xff else None)


DOCKER = "127.0.0.0/8,::1,192.168.80.0/24"


# ── the untrusted case: the header must not matter ──────────────────────

def test_direct_caller_is_used_as_is():
    assert client_address(req("203.0.113.7")) == "203.0.113.7"


def test_forwarded_header_from_an_untrusted_peer_is_ignored():
    """⚠️ THE WHOLE SECURITY PROPERTY.

    A direct caller can put anything in this header. Believing it would let
    one client present a fresh address per request and evade every limit.
    """
    assert client_address(req("203.0.113.7", xff="1.2.3.4")) == "203.0.113.7"


def test_spoofed_chain_from_an_untrusted_peer_is_ignored():
    assert client_address(
        req("203.0.113.7", xff="1.2.3.4, 5.6.7.8, 9.10.11.12")) == "203.0.113.7"


def test_empty_trust_list_trusts_nothing():
    """The safe failure mode: with nothing configured, behave exactly as
    before the header was ever read."""
    assert client_address(req("127.0.0.1", xff="1.2.3.4"), "") == "127.0.0.1"


# ── the trusted case ────────────────────────────────────────────────────

def test_loopback_proxy_is_trusted_by_default():
    """A loopback peer can only be a process on this machine, so its
    forwarded header is ours."""
    assert client_address(req("127.0.0.1", xff="203.0.113.7")) == "203.0.113.7"


def test_docker_bridge_once_configured():
    """The prime case. Caddy on the host, node in a container."""
    assert client_address(
        req("192.168.80.1", xff="203.0.113.7"), DOCKER) == "203.0.113.7"


def test_rightmost_untrusted_entry_wins():
    """⚠️ RIGHT-TO-LEFT IS THE ONLY CORRECT DIRECTION.

    Each hop appends, so the left-hand entries are whatever the original
    client chose to send. Taking the first entry means an attacker writes
    `X-Forwarded-For: 1.2.3.4` and our proxy appends their real address after
    it — and we would report the value they invented.
    """
    assert client_address(
        req("127.0.0.1", xff="1.2.3.4, 203.0.113.7"), DEFAULT_TRUSTED_PROXIES
    ) == "203.0.113.7"


def test_chained_trusted_proxies_are_skipped():
    """Two of our own hops, then the real caller."""
    assert client_address(
        req("192.168.80.1", xff="203.0.113.7, 192.168.80.5, 127.0.0.1"), DOCKER
    ) == "203.0.113.7"


def test_all_hops_trusted_returns_the_peer():
    """A request that genuinely originated inside the trusted set. The nearest
    trusted address is the true answer — there is no external client."""
    assert client_address(
        req("127.0.0.1", xff="127.0.0.1, 127.0.0.1"), DEFAULT_TRUSTED_PROXIES
    ) == "127.0.0.1"


def test_trusted_proxy_with_no_header_returns_the_proxy():
    """Honest rather than invented. If the proxy forwards nothing, its own
    address is all we know."""
    assert client_address(req("127.0.0.1"), DEFAULT_TRUSTED_PROXIES) == "127.0.0.1"


def test_garbage_entries_are_skipped_not_returned():
    """A malformed hop must never become someone's rate-limit key."""
    assert client_address(
        req("127.0.0.1", xff="203.0.113.7, not-an-ip"), DEFAULT_TRUSTED_PROXIES
    ) == "203.0.113.7"


def test_whitespace_and_empty_entries_tolerated():
    assert client_address(
        req("127.0.0.1", xff="  203.0.113.7 ,, "), DEFAULT_TRUSTED_PROXIES
    ) == "203.0.113.7"


def test_ipv6_loopback_is_trusted():
    assert client_address(req("::1", xff="2001:db8::5"),
                          DEFAULT_TRUSTED_PROXIES) == "2001:db8::5"


# ── degenerate inputs ───────────────────────────────────────────────────

def test_missing_client_is_unknown():
    assert client_address(FakeRequest(None)) == "unknown"


def test_unparseable_peer_is_never_trusted():
    """"unknown" is not an address; it must not accidentally match a network."""
    assert client_address(FakeRequest("unknown", {"X-Forwarded-For": "1.2.3.4"}),
                          DEFAULT_TRUSTED_PROXIES) == "unknown"


# ── parsing the config ──────────────────────────────────────────────────

def test_parse_accepts_cidrs_and_bare_addresses():
    assert len(parse_trusted("127.0.0.0/8, ::1, 192.168.80.1")) == 3


def test_parse_skips_typos_rather_than_raising():
    """A typo in one entry must not take the node down — and skipping yields
    LESS trust, not more, which is the right direction to fail."""
    nets = parse_trusted("127.0.0.0/8, not-a-network, ::1")
    assert len(nets) == 2


def test_parse_of_empty_is_empty():
    assert parse_trusted("") == []
    assert parse_trusted("   ,  ") == []
