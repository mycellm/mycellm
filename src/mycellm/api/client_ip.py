"""Who is actually calling — behind a reverse proxy, and safely.

⚠️ EVERY PER-IP LIMIT IN THIS CODEBASE WAS ONE SHARED BUCKET ON THE PUBLIC
BOOTSTRAP. `request.client.host` is the TCP peer, and prime runs behind Caddy
in Docker, so every caller on earth arrived as `192.168.80.1` — the bridge
gateway. Confirmed live: the node registry had *every* announcing node recorded
at that one address.

The consequences were not cosmetic:

- Public chat's anon rate limit (30/min per IP) applied to the whole internet
  at once, so a handful of visitors could lock everyone out.
- The announce limiter (10 new nodes per minute per IP) throttled the entire
  network as if it were one host.
- Registered `api_addr` values pointed at the bridge, so the gateway's HTTP
  fallback could only ever burn a connect timeout before failing over.

The fix is to read `X-Forwarded-For` — but **only from a proxy we trust**,
because that header is caller-supplied and trusting it blindly turns every
rate limit into a suggestion (spoof a fresh IP per request and no limit ever
applies).

What makes this safe is that `request.client.host` is the peer of an
*established TCP connection* and therefore cannot be spoofed. If that peer is
inside the configured trusted set, the connection genuinely came from our own
reverse proxy and its forwarded header is worth reading. If it is not, the
header is ignored entirely.

Default is loopback only. Anything wider — a Docker bridge, a separate proxy
host — must be configured deliberately via `MYCELLM_TRUSTED_PROXIES`, because
the operator is the only one who knows which addresses are really theirs.
"""

from __future__ import annotations

import ipaddress
import logging

logger = logging.getLogger("mycellm.api")

#: Safe everywhere: a loopback peer can only be a process on this machine.
DEFAULT_TRUSTED_PROXIES = "127.0.0.0/8,::1"

_UNKNOWN = "unknown"


def parse_trusted(spec: str) -> list:
    """Parse a comma-separated list of IPs/CIDRs into networks.

    Unparseable entries are skipped with a warning rather than raising: a typo
    in one entry must not take the node down, and the failure mode of skipping
    is *less* trust, not more.
    """
    networks = []
    for raw in (spec or "").split(","):
        entry = raw.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning(f"Ignoring unparseable trusted proxy entry: {entry!r}")
    return networks


def _is_trusted(addr: str, trusted: list) -> bool:
    if not trusted:
        return False
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in trusted)


def client_address(request, trusted_spec: str = DEFAULT_TRUSTED_PROXIES) -> str:
    """The caller's real address.

    Returns the TCP peer unless that peer is a trusted proxy, in which case the
    right-most **untrusted** entry of `X-Forwarded-For` is used.

    Right-to-left is the only correct direction. The header is a list appended
    to by each hop, so the left-hand entries are whatever the *original client*
    chose to send — an attacker writes `X-Forwarded-For: 1.2.3.4` and every
    proxy dutifully appends the real address after it. Walking from the right
    and stopping at the first address we did not put there ourselves is what
    makes the value trustworthy.
    """
    peer = request.client.host if request.client else _UNKNOWN
    trusted = parse_trusted(trusted_spec)

    if not _is_trusted(peer, trusted):
        return peer

    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded:
        # A trusted proxy that forwards no header leaves us with the proxy's
        # own address. Reporting it is honest — inventing one would not be.
        return peer

    for candidate in reversed([p.strip() for p in forwarded.split(",") if p.strip()]):
        if not _is_trusted(candidate, trusted):
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue  # garbage entry; keep walking left
            return candidate

    # Every hop was ours. That means the request originated inside the trusted
    # set, so the nearest trusted address is the true answer.
    return peer
