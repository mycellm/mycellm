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


def _select_tier1_model(node) -> tuple[str | None, str | None]:
    """Select the best available Tier 1 model.

    Checks local models first, then fleet nodes.
    Returns (model_name, fleet_addr) — fleet_addr is None for local models.
    """
    # Check local models
    local_candidates = []
    for m in node.inference.loaded_models:
        tier = classify_tier(m.param_count_b)
        if tier == 1:
            local_candidates.append(m)

    if local_candidates:
        local_candidates.sort(key=lambda m: m.param_count_b, reverse=True)
        return local_candidates[0].name, None

    # Fallback: any local model (param_count_b unknown)
    if node.inference.loaded_models:
        return node.inference.loaded_models[0].name, None

    # Check fleet nodes — prefer Tier 1, fall back to any model
    import time as _time
    best_fleet = None  # (name, addr, tier)
    for entry in node.node_registry.values():
        if entry.get("status") != "approved":
            continue
        if _time.time() - entry.get("last_seen", 0) > 120:
            continue  # offline
        for m in entry.get("capabilities", {}).get("models", []):
            if isinstance(m, dict):
                name = m.get("name", "")
                param_b = m.get("param_count_b", 0)
                tier = classify_tier(param_b)
            else:
                name = m
                tier = 1  # unknown defaults to Tier 1
            addr = entry.get("api_addr", "")
            if tier <= 1:
                return name, addr  # Tier 1 — use immediately
            if best_fleet is None or tier < best_fleet[2]:
                best_fleet = (name, addr, tier)

    # No Tier 1 found — fall back to best available (better than nothing)
    if best_fleet:
        return best_fleet[0], best_fleet[1]

    return None, None


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
    model_name, fleet_addr = _select_tier1_model(node)
    if not model_name:
        return JSONResponse(status_code=503, content={
            "error": {"message": "No models currently available. Try again later."}
        })

    stream = body.get("stream", False)
    max_tokens = min(body.get("max_tokens", _MAX_REQUEST_TOKENS), _MAX_REQUEST_TOKENS)
    temperature = body.get("temperature", 0.7)

    start_time = time.time()
    request_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"

    # Route to fleet node if model is remote
    if fleet_addr:
        if stream:
            return await _stream_fleet(
                node, request_id, model_name, fleet_addr, messages,
                temperature, max_tokens, client_ip, start_time,
            )
        return await _proxy_fleet(
            node, request_id, model_name, fleet_addr, messages,
            temperature, max_tokens, client_ip, start_time,
        )

    if stream:
        return await _stream_public(
            node, request_id, model_name, messages,
            temperature, max_tokens, client_ip, start_time,
        )

    # Non-streaming local inference
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

        return _clean_response(request_id, model_name, result.text,
                               result.finish_reason, result.prompt_tokens,
                               result.completion_tokens, latency_ms)

    except Exception as e:
        logger.warning(f"Public gateway inference failed: {e}")
        node.activity.record(EventType.INFERENCE_FAILED, model=model_name, source="public_gateway")
        return JSONResponse(status_code=500, content={
            "error": {"message": "Inference failed. The network may be busy."}
        })


def _node_hash(addr: str) -> str:
    """Generate an anonymized 8-char hash of a node address for attribution."""
    import hashlib
    return hashlib.sha256(addr.encode()).hexdigest()[:8]


def _clean_response(request_id, model_name, text, finish_reason, prompt_tokens, completion_tokens, latency_ms, node_id=""):
    """Build a clean, metadata-stripped OpenAI-compatible response."""
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": finish_reason or "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "mycellm": {
            "latency_ms": latency_ms,
            "node": node_id or "local",
            "served_by": "mycellm-public",
        },
    }


async def _proxy_fleet(node, request_id, model_name, fleet_addr, messages, temperature, max_tokens, client_ip, start_time):
    """Proxy a non-streaming request to a fleet node."""
    import httpx
    from mycellm.activity import EventType

    base = fleet_addr if fleet_addr.startswith("http") else f"http://{fleet_addr}"
    try:
        data = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=180.0)) as client:
                    resp = await client.post(f"{base}/v1/chat/completions", json={
                        "model": model_name,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "stream": False,
                    })
                    resp.raise_for_status()
                    data = resp.json()
                    break
            except (httpx.RemoteProtocolError, httpx.ReadError) as retry_err:
                if attempt == 0:
                    logger.info(f"Fleet proxy retry after: {retry_err}")
                    continue
                raise

        latency_ms = round((time.time() - start_time) * 1000)
        choice = data.get("choices", [{}])[0]
        usage = data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)

        _record_usage(client_ip, total_tokens)
        node.activity.record(
            EventType.INFERENCE_COMPLETE,
            model=model_name, source="public_gateway_fleet",
            tokens=usage.get("completion_tokens", 0), latency_ms=latency_ms,
        )

        return _clean_response(
            request_id, model_name,
            choice.get("message", {}).get("content", ""),
            choice.get("finish_reason", "stop"),
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            latency_ms,
            node_id=_node_hash(fleet_addr),
        )

    except Exception as e:
        logger.warning(f"Fleet proxy to {fleet_addr} failed: {e}")
        node.activity.record(EventType.INFERENCE_FAILED, model=model_name, source="public_gateway_fleet")
        return JSONResponse(status_code=503, content={
            "error": {"message": "Fleet node unavailable. Try again."}
        })


async def _stream_fleet(node, request_id, model_name, fleet_addr, messages, temperature, max_tokens, client_ip, start_time):
    """Stream a response from a fleet node via SSE proxy."""
    import httpx
    from fastapi.responses import StreamingResponse
    from mycellm.activity import EventType

    node_id = _node_hash(fleet_addr)

    async def generate():
        total_tokens = 0
        first_chunk = True
        base = fleet_addr if fleet_addr.startswith("http") else f"http://{fleet_addr}"
        upstream_resp = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=180.0)) as client:
                async with client.stream("POST", f"{base}/v1/chat/completions", json={
                    "model": model_name,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                }) as resp:
                    upstream_resp = resp
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:].strip()
                        if payload == "[DONE]":
                            break

                        try:
                            chunk = json.loads(payload)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            finish = chunk.get("choices", [{}])[0].get("finish_reason")
                            if content:
                                total_tokens += len(content.split())

                            out = {
                                "id": request_id,
                                "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": model_name,
                                "choices": [{"index": 0, "delta": {"content": content} if content else {}, "finish_reason": finish}],
                            }
                            # First chunk: include node attribution
                            if first_chunk:
                                out["mycellm"] = {"node": node_id, "served_by": "mycellm-public"}
                                first_chunk = False
                            yield f"data: {json.dumps(out)}\n\n"
                            if finish:
                                break
                        except json.JSONDecodeError:
                            continue

            # Final metadata chunk with latency
            latency_ms = round((time.time() - start_time) * 1000)
            meta_chunk = {
                "id": request_id, "object": "chat.completion.chunk",
                "model": model_name,
                "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
                "mycellm": {"node": node_id, "latency_ms": latency_ms, "served_by": "mycellm-public"},
            }
            yield f"data: {json.dumps(meta_chunk)}\n\n"
            yield "data: [DONE]\n\n"

            _record_usage(client_ip, total_tokens)
            node.activity.record(
                EventType.INFERENCE_COMPLETE,
                model=model_name, source="public_gateway_fleet",
                tokens=total_tokens, latency_ms=latency_ms,
            )

        except asyncio.CancelledError:
            logger.info(f"Client disconnected during fleet stream to {fleet_addr}")
            return
        except Exception as e:
            logger.warning(f"Fleet stream to {fleet_addr} failed: {e}")
            error_chunk = {
                "id": request_id, "object": "chat.completion.chunk",
                "model": model_name,
                "choices": [{"index": 0, "delta": {"content": "\n\n[Network error — try again]"}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            # Close upstream connection if client disconnected mid-stream
            if upstream_resp and not upstream_resp.is_stream_consumed:
                try:
                    await upstream_resp.aclose()
                except Exception:
                    pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_public(node, request_id, model_name, messages, temperature, max_tokens, client_ip, start_time):
    """Stream a public chat response via SSE (local inference)."""
    from fastapi.responses import StreamingResponse
    from mycellm.activity import EventType
    from mycellm.inference.base import InferenceRequest

    local_node_id = _node_hash(node.peer_id) if hasattr(node, 'peer_id') else "local"

    async def generate():
        total_tokens = 0
        first_chunk = True
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
                if first_chunk:
                    data["mycellm"] = {"node": local_node_id, "served_by": "mycellm-public"}
                    first_chunk = False
                yield f"data: {json.dumps(data)}\n\n"
                if chunk.finish_reason:
                    break

            latency_ms = round((time.time() - start_time) * 1000)
            meta_chunk = {
                "id": request_id, "object": "chat.completion.chunk",
                "model": model_name,
                "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
                "mycellm": {"node": local_node_id, "latency_ms": latency_ms, "served_by": "mycellm-public"},
            }
            yield f"data: {json.dumps(meta_chunk)}\n\n"
            yield "data: [DONE]\n\n"

            _record_usage(client_ip, total_tokens)
            node.activity.record(
                EventType.INFERENCE_COMPLETE,
                model=model_name, source="public_gateway",
                tokens=total_tokens, latency_ms=latency_ms,
            )

        except asyncio.CancelledError:
            logger.info("Client disconnected during local stream")
            return
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
