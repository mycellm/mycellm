"""PeerRegistry — in-memory validated peer state indexed by model."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from mycellm.protocol.capabilities import Capabilities
from mycellm.transport.connection import PeerConnection, PeerState

logger = logging.getLogger("mycellm.router")


@dataclass
class PeerEntry:
    """A tracked peer with its connection and metadata."""

    peer_id: str
    connection: PeerConnection | None = None
    capabilities: Capabilities = field(default_factory=Capabilities)
    state: PeerState = PeerState.DISCOVERED
    addresses: list[str] = field(default_factory=list)
    last_seen: float = field(default_factory=time.time)
    failure_count: int = 0
    network_ids: list[str] = field(default_factory=list)
    address_scores: dict[str, int] = field(default_factory=dict)  # addr -> success count

    def record_address_success(self, addr: str) -> None:
        self.address_scores[addr] = self.address_scores.get(addr, 0) + 1

    def record_address_failure(self, addr: str) -> None:
        self.address_scores[addr] = max(self.address_scores.get(addr, 0) - 1, -3)

    def sorted_addresses(self) -> list[str]:
        """Return addresses sorted by score (best first)."""
        return sorted(self.addresses, key=lambda a: self.address_scores.get(a, 0), reverse=True)


class PeerRegistry:
    """In-memory peer registry indexed by model capability."""

    def __init__(self):
        self._peers: dict[str, PeerEntry] = {}  # peer_id -> PeerEntry
        self._model_index: dict[str, set[str]] = {}  # model_name -> set of peer_ids

    def register(
        self,
        peer_id: str,
        connection: PeerConnection | None = None,
        capabilities: Capabilities | None = None,
        addresses: list[str] | None = None,
        network_ids: list[str] | None = None,
    ) -> PeerEntry:
        """Register or update a peer.

        network_ids: the networks this peer declared membership in (from its
        NodeHello). Used to keep inference/routing within a shared network —
        a request on network A must not be served by a peer only on network B.
        """
        entry = self._peers.get(peer_id)
        if entry is None:
            entry = PeerEntry(peer_id=peer_id)
            self._peers[peer_id] = entry

        if connection:
            entry.connection = connection
            entry.state = connection.state
        if capabilities:
            # Remove old model index entries
            self._remove_from_model_index(peer_id)
            entry.capabilities = capabilities
            # Add new model index entries
            for model in capabilities.models:
                self._model_index.setdefault(model.name, set()).add(peer_id)
        if addresses:
            entry.addresses = addresses
        if network_ids is not None:
            entry.network_ids = list(network_ids)

        entry.last_seen = time.time()
        return entry

    def unregister(self, peer_id: str) -> None:
        self._remove_from_model_index(peer_id)
        self._peers.pop(peer_id, None)

    def get(self, peer_id: str) -> PeerEntry | None:
        return self._peers.get(peer_id)

    def peers_for_model(
        self, model_name: str, network_ids: list[str] | None = None
    ) -> list[PeerEntry]:
        """Get all peers that can serve a given model.

        network_ids: if given, only return peers that share at least one network
        with the requester (network isolation). A peer with no declared networks
        is treated as public/legacy and always eligible, so single-network
        deployments and un-upgraded peers keep working.
        """
        want = set(network_ids) if network_ids else None
        peer_ids = self._model_index.get(model_name, set())
        entries = []
        for pid in peer_ids:
            entry = self._peers.get(pid)
            # Accept any peer that has completed the NodeHello handshake.
            # ROUTABLE is the steady-state but a freshly-authenticated peer
            # is also able to serve — the only state that should disqualify
            # them is DISCOVERED (no live connection yet).
            if not entry or entry.state not in (
                PeerState.AUTHENTICATED,
                PeerState.ROUTABLE,
                PeerState.SERVING,
            ):
                continue
            if want is not None and entry.network_ids and not (want & set(entry.network_ids)):
                continue  # no shared network — not reachable from this requester
            entries.append(entry)
        return entries

    def all_peers(self) -> list[PeerEntry]:
        return list(self._peers.values())

    def connected_peers(self) -> list[PeerEntry]:
        return [
            p for p in self._peers.values()
            if p.state in (PeerState.AUTHENTICATED, PeerState.ROUTABLE, PeerState.SERVING)
        ]

    def peers_for_tag(self, tag: str) -> list[PeerEntry]:
        """Get all routable peers that have models with a given tag."""
        entries = []
        for entry in self._peers.values():
            if entry.state not in (PeerState.ROUTABLE, PeerState.SERVING):
                continue
            for m in entry.capabilities.models:
                if tag.lower() in [t.lower() for t in getattr(m, 'tags', [])]:
                    entries.append(entry)
                    break
        return entries

    def peers_for_tier(self, tier: str) -> list[PeerEntry]:
        """Get all routable peers that have models in a given tier."""
        entries = []
        for entry in self._peers.values():
            if entry.state not in (PeerState.ROUTABLE, PeerState.SERVING):
                continue
            for m in entry.capabilities.models:
                if getattr(m, 'tier', '') == tier.lower():
                    entries.append(entry)
                    break
        return entries

    def all_models(self) -> list[tuple[str, str]]:
        """Get all (model_name, peer_id) tuples across the network."""
        result = []
        for entry in self._peers.values():
            if entry.state not in (PeerState.ROUTABLE, PeerState.SERVING):
                continue
            for m in entry.capabilities.models:
                result.append((m.name, entry.peer_id))
        return result

    def peers_for_network(self, network_id: str) -> list[PeerEntry]:
        """Get all peers that belong to a specific network."""
        return [
            p for p in self._peers.values()
            if network_id in p.network_ids
            and p.state in (PeerState.ROUTABLE, PeerState.SERVING)
        ]

    def models_visible_to_network(self, network_id: str) -> list[tuple[str, str]]:
        """Get (model_name, peer_id) tuples visible to a network."""
        result = []
        for entry in self._peers.values():
            if entry.state not in (PeerState.ROUTABLE, PeerState.SERVING):
                continue
            for m in entry.capabilities.models:
                scope = getattr(m, 'scope', 'home')
                visible = getattr(m, 'visible_networks', [])
                if scope == "public":
                    result.append((m.name, entry.peer_id))
                elif scope == "networks" and network_id in visible:
                    result.append((m.name, entry.peer_id))
                elif scope == "home" and network_id in entry.network_ids:
                    result.append((m.name, entry.peer_id))
        return result

    def _remove_from_model_index(self, peer_id: str) -> None:
        for model_peers in self._model_index.values():
            model_peers.discard(peer_id)
