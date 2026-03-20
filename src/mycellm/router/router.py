"""Main router — receives inference requests, selects peer, forwards, returns response."""

from __future__ import annotations

import logging

from mycellm.protocol.envelope import MessageEnvelope, MessageType
from mycellm.protocol.errors import ErrorCode, ProtocolError
from mycellm.router.chain import ChainBuilder
from mycellm.router.registry import PeerRegistry

logger = logging.getLogger("mycellm.router")

MAX_RETRY_ATTEMPTS = 3


class Router:
    """Routes inference requests to appropriate peers with failover."""

    def __init__(self, registry: PeerRegistry):
        self._registry = registry
        self._chain_builder = ChainBuilder(registry)

    async def route_inference(
        self, model: str, messages: list[dict], **kwargs
    ) -> MessageEnvelope:
        """Route an inference request with automatic failover.

        Tries candidates in order. On failure, moves to next candidate.
        Error-specific behavior:
        - OVERLOADED -> skip to next immediately
        - TIMEOUT -> retry once, then next
        """
        targets = self._chain_builder.route(model)
        if not targets:
            raise ProtocolError(ErrorCode.MODEL_UNAVAILABLE, f"No peers serving model '{model}'")

        last_error = None
        attempts = 0

        for target in targets:
            if attempts >= MAX_RETRY_ATTEMPTS:
                break

            if target.entry.connection is None:
                continue

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

                if response.type == MessageType.ERROR:
                    error_code = response.payload.get("error_code", "")
                    if error_code == ErrorCode.OVERLOADED.value:
                        logger.debug(f"Peer {target.peer_id[:8]} overloaded, trying next")
                        attempts += 1
                        continue
                    # Other errors are fatal for this request
                    target.entry.failure_count += 1
                    last_error = ProtocolError(
                        ErrorCode(error_code) if error_code else ErrorCode.UNKNOWN,
                        response.payload.get("error_message", ""),
                    )
                    attempts += 1
                    continue

                # Success — reset failure count
                target.entry.failure_count = max(0, target.entry.failure_count - 1)
                return response

            except TimeoutError:
                target.entry.failure_count += 1
                logger.warning(f"Timeout routing to {target.peer_id[:8]}")
                attempts += 1
                last_error = ProtocolError(ErrorCode.TIMEOUT, f"Timeout routing to {target.peer_id}")
                continue
            except Exception as e:
                target.entry.failure_count += 1
                logger.error(f"Inference routing to {target.peer_id[:8]} failed: {e}")
                attempts += 1
                last_error = ProtocolError(ErrorCode.BACKEND_ERROR, str(e))
                continue

        if last_error:
            raise last_error
        raise ProtocolError(ErrorCode.PEER_UNREACHABLE, "All peers unreachable")
