"""Main router — receives inference requests, selects peer, forwards, returns response."""

from __future__ import annotations

import logging

from mycellm.protocol.envelope import MessageEnvelope, MessageType
from mycellm.protocol.errors import ErrorCode, ProtocolError
from mycellm.router.chain import ChainBuilder
from mycellm.router.registry import PeerRegistry

logger = logging.getLogger("mycellm.router")


class Router:
    """Routes inference requests to appropriate peers."""

    def __init__(self, registry: PeerRegistry):
        self._registry = registry
        self._chain_builder = ChainBuilder(registry)

    async def route_inference(
        self, model: str, messages: list[dict], **kwargs
    ) -> MessageEnvelope:
        """Route an inference request to the best available peer.

        Returns the response envelope from the peer.
        """
        targets = self._chain_builder.route(model)
        if not targets:
            raise ProtocolError(ErrorCode.MODEL_UNAVAILABLE, f"No peers serving model '{model}'")

        target = targets[0]
        if target.entry.connection is None:
            raise ProtocolError(ErrorCode.PEER_UNREACHABLE, f"No connection to peer {target.peer_id}")

        request = MessageEnvelope(
            type=MessageType.INFERENCE_REQ,
            payload={
                "model": model,
                "messages": messages,
                **kwargs,
            },
        )

        try:
            response = await target.entry.connection.request(request)
            return response
        except Exception as e:
            target.entry.failure_count += 1
            logger.error(f"Inference routing to {target.peer_id} failed: {e}")
            raise ProtocolError(ErrorCode.BACKEND_ERROR, str(e))
