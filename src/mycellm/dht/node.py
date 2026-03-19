"""Kademlia DHT node for peer discovery (hints only)."""

from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger("mycellm.dht")


class DHTNode:
    """Kademlia DHT wrapper for peer discovery.

    IMPORTANT: DHT data is untrusted. Always fetch fresh signed
    capabilities over authenticated transport channel.
    """

    def __init__(self, port: int = 8422):
        self.port = port
        self._server = None
        self._running = False

    async def start(self, bootstrap_peers: list[tuple[str, int]] | None = None) -> None:
        """Start the DHT node."""
        from kademlia.network import Server

        self._server = Server()
        await self._server.listen(self.port)
        self._running = True

        if bootstrap_peers:
            await self._server.bootstrap(bootstrap_peers)
            logger.info(f"DHT bootstrapped with {len(bootstrap_peers)} peers")
        else:
            logger.info(f"DHT listening on port {self.port} (no bootstrap peers)")

    async def announce(self, peer_id: str, addresses: list[str], capabilities_hint: dict) -> None:
        """Announce this node's presence on the DHT.

        This is a hint only — peers must verify over authenticated transport.
        """
        if not self._server:
            return

        value = json.dumps({
            "peer_id": peer_id,
            "addresses": addresses,
            "hint": capabilities_hint,
        })
        await self._server.set(peer_id, value)
        logger.debug(f"Announced {peer_id} on DHT")

    async def discover(self, peer_id: str) -> dict | None:
        """Look up a peer on the DHT.

        Returns untrusted discovery hint — must verify over transport.
        """
        if not self._server:
            return None

        result = await self._server.get(peer_id)
        if result:
            return json.loads(result)
        return None

    async def find_peers(self) -> list[dict]:
        """Discover peers from DHT neighbors.

        Returns list of untrusted hints.
        """
        # In Phase 1, peers are found via bootstrap list.
        # DHT discovery is supplementary.
        return []

    async def stop(self) -> None:
        if self._server:
            self._server.stop()
            self._running = False
            logger.info("DHT stopped")
