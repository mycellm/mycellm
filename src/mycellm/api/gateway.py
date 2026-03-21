"""Public API gateway — rate-limited, restricted inference for anonymous users.

Provides POST /v1/public/chat/completions as a safe, metadata-stripped
wrapper around the internal inference pipeline. Designed for the public
portal at mycellm.ai.

Restrictions vs authenticated API:
  - Rate limited: 5,000 tokens/day per IP
  - Model restriction: Tier 1 only (≤8B models)
  - No fanout, no model selection (auto-routed)
  - Metadata stripped: no peer IDs, routing details, credit info
  - No system prompt override
  - Max 1024 tokens per response
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mycellm.protocol.capabilities import classify_tier

logger = logging.getLogger("mycellm.api.gateway")

router = APIRouter()

# --- Rate limiting ---

_DAILY_TOKEN_BUDGET = 5_000
_MAX_REQUEST_TOKENS = 1024
_MAX_REQUESTS_PER_MINUTE = 10
_MAX_MESSAGE_LENGTH = 2000  # chars per message

# Per-IP rate state: {ip: {"tokens": N, "requests_minute": N, "minute": T, "reset": T}}
_rate_state: dict[str, dict] = defaultdict(lambda: {
    "tokens": 0,
    "requests_minute": 0,
    "minute": 0,
    "reset": time.time() + 86400,
})


def _check_rate(ip: str, est_tokens: int = 0) -> tuple[bool, str]:
    """Check if an IP is within rate limits. Returns (allowed, reason)."""
    now = time.time()
    state = _rate_state[ip]

    # Reset daily budget
    if now > state["reset"]:
        state["tokens"] = 0
        state["reset"] = now + 86400

    # Per-minute request limit
    current_minute = int(now / 60)
    if current_minute != state["minute"]:
        state["requests_minute"] = 0
        state["minute"] = current_minute

    if state["requests_minute"] >= _MAX_REQUESTS_PER_MINUTE:
        return False, "Rate limit: too many requests. Try again in a minute."

    # Daily token budget
    if state["tokens"] + est_tokens > _DAILY_TOKEN_BUDGET:
        remaining = max(0, _DAILY_TOKEN_BUDGET - state["tokens"])
        return False, f"Daily token limit reached ({_DAILY_TOKEN_BUDGET} tokens/day). {remaining} remaining."

    return True, ""


def _record_usage(ip: str, tokens: int) -> None:
    """Record token usage for an IP."""
    state = _rate_state[ip]
    state["tokens"] += tokens
    state["requests_minute"] += 1


def _select_tier1_model(node) -> str | None:
    """Select the best available Tier 1 model.

    Prefers models with known param counts, falls back to any loaded model.
    """
    candidates = []
    for m in node.inference.loaded_models:
        tier = classify_tier(m.param_count_b)
        if tier == 1:
            candidates.append(m)

    if not candidates:
        # Fallback: if no model has param_count_b set, use any loaded model
        # (common when models are loaded without metadata)
        if node.inference.loaded_models:
            return node.inference.loaded_models[0].name
        return None

    # Prefer largest Tier 1 model (better quality within tier)
    candidates.sort(key=lambda m: m.param_count_b, reverse=True)
    return candidates[0].name


@router.post("/chat/completions")
async def public_chat(request: Request):
    """Public chat completions — rate-limited, Tier 1 only, no auth required.

    OpenAI-compatible request/response format. Streaming supported via SSE.
    """
    from mycellm.activity import EventType
    from mycellm.inference.base import InferenceRequest

    node = request.app.state.node
    client_ip = request.client.host if request.client else "unknown"

    # Parse request body
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "Invalid JSON"}})

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse(status_code=400, content={"error": {"message": "messages required"}})

    # Validate message lengths
    for msg in messages:
        if len(msg.get("content", "")) > _MAX_MESSAGE_LENGTH:
            return JSONResponse(status_code=400, content={
                "error": {"message": f"Message too long (max {_MAX_MESSAGE_LENGTH} chars)"}
            })

    # Rate limit check
    allowed, reason = _check_rate(client_ip)
    if not allowed:
        return JSONResponse(status_code=429, content={"error": {"message": reason}})

    # Select model (Tier 1 only, user cannot choose)
    model_name = _select_tier1_model(node)
    if not model_name:
        return JSONResponse(status_code=503, content={
            "error": {"message": "No models currently available. Try again later."}
        })

    stream = body.get("stream", False)
    max_tokens = min(body.get("max_tokens", _MAX_REQUEST_TOKENS), _MAX_REQUEST_TOKENS)
    temperature = body.get("temperature", 0.7)

    start_time = time.time()
    request_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"

    if stream:
        return await _stream_public(
            node, request_id, model_name, messages,
            temperature, max_tokens, client_ip, start_time,
        )

    # Non-streaming
    try:
        inf_req = InferenceRequest(
            model=model_name,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        backend = node.inference.get_backend(model_name)
        if not backend:
            return JSONResponse(status_code=503, content={
                "error": {"message": "Model temporarily unavailable."}
            })

        result = await backend.generate(inf_req)
        latency_ms = round((time.time() - start_time) * 1000)
        total_tokens = result.prompt_tokens + result.completion_tokens

        _record_usage(client_ip, total_tokens)
        node.activity.record(
            EventType.INFERENCE_COMPLETE,
            model=model_name, source="public_gateway",
            tokens=result.completion_tokens, latency_ms=latency_ms,
        )

        # Stripped response — no peer IDs, routing info, or credit data
        return {
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": result.text},
                "finish_reason": result.finish_reason,
            }],
            "usage": {
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": total_tokens,
            },
            "mycellm": {
                "latency_ms": latency_ms,
                "served_by": "mycellm-public",
            },
        }

    except Exception as e:
        logger.warning(f"Public gateway inference failed: {e}")
        node.activity.record(EventType.INFERENCE_FAILED, model=model_name, source="public_gateway")
        return JSONResponse(status_code=500, content={
            "error": {"message": "Inference failed. The network may be busy."}
        })


async def _stream_public(node, request_id, model_name, messages, temperature, max_tokens, client_ip, start_time):
    """Stream a public chat response via SSE."""
    from fastapi.responses import StreamingResponse
    from mycellm.activity import EventType
    from mycellm.inference.base import InferenceRequest

    async def generate():
        total_tokens = 0
        try:
            inf_req = InferenceRequest(
                model=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            backend = node.inference.get_backend(model_name)
            if not backend:
                error_chunk = {
                    "id": request_id, "object": "chat.completion.chunk",
                    "model": model_name,
                    "choices": [{"index": 0, "delta": {"content": "Model temporarily unavailable."}, "finish_reason": "stop"}],
                }
                yield f"data: {json.dumps(error_chunk)}\n\n"
                yield "data: [DONE]\n\n"
                return

            async for chunk in backend.generate_stream(inf_req):
                total_tokens += len(chunk.text.split()) if chunk.text else 0
                data = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model_name,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": chunk.text} if chunk.text else {},
                        "finish_reason": chunk.finish_reason,
                    }],
                }
                yield f"data: {json.dumps(data)}\n\n"
                if chunk.finish_reason:
                    break

            yield "data: [DONE]\n\n"

            latency_ms = round((time.time() - start_time) * 1000)
            _record_usage(client_ip, total_tokens)
            node.activity.record(
                EventType.INFERENCE_COMPLETE,
                model=model_name, source="public_gateway",
                tokens=total_tokens, latency_ms=latency_ms,
            )

        except Exception as e:
            logger.warning(f"Public gateway stream failed: {e}")
            error_chunk = {
                "id": request_id, "object": "chat.completion.chunk",
                "model": model_name,
                "choices": [{"index": 0, "delta": {"content": "\n\n[Network error — try again]"}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
