"""Kademlia DHT node for peer discovery (hints only)."""

from __future__ import annotations

import asyncio
import json
import logging
import time

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

    async def announce_model(self, model_name: str, peer_id: str, addresses: list[str]) -> None:
        """Announce that this peer serves a specific model."""
        if not self._server:
            return
        key = f"model:{model_name}"
        existing = await self._server.get(key)
        peers = json.loads(existing) if existing else []

        # Update or add this peer
        updated = False
        for p in peers:
            if p.get("peer_id") == peer_id:
                p["addresses"] = addresses
                p["ts"] = time.time()
                updated = True
                break
        if not updated:
            peers.append({"peer_id": peer_id, "addresses": addresses, "ts": time.time()})

        # Prune stale entries (>10 min old)
        cutoff = time.time() - 600
        peers = [p for p in peers if p.get("ts", 0) > cutoff]

        await self._server.set(key, json.dumps(peers))
        logger.debug(f"Announced model:{model_name} on DHT ({len(peers)} peers)")

    async def find_model_peers(self, model_name: str) -> list[dict]:
        """Find peers serving a specific model via DHT.

        Returns list of untrusted hints: [{peer_id, addresses, ts}]
        """
        if not self._server:
            return []
        key = f"model:{model_name}"
        result = await self._server.get(key)
        if result:
            try:
                peers = json.loads(result)
                # Filter stale
                cutoff = time.time() - 600
                return [p for p in peers if p.get("ts", 0) > cutoff]
            except json.JSONDecodeError:
                return []
        return []

    async def find_peers(self) -> list[dict]:
        """Discover peers from DHT neighbors.

        Returns list of untrusted hints.
        """
        if not self._server:
            return []
        # Query for known peer announcements
        results = []
        try:
            # Get neighbors from the routing table
            for bucket in self._server.protocol.router.buckets:
                for node in bucket.get_nodes():
                    peer_data = await self._server.get(node.id.hex())
                    if peer_data:
                        try:
                            results.append(json.loads(peer_data))
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            logger.debug(f"DHT find_peers error: {e}")
        return results

    async def stop(self) -> None:
        if self._server:
            self._server.stop()
            self._running = False
            logger.info("DHT stopped")
