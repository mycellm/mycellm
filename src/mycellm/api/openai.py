"""OpenAI-compatible chat completions API."""

from __future__ import annotations

import json
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = ""
    messages: list[ChatMessage]
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    stream: bool = False
    top_p: float = 1.0


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:8]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = ""
    choices: list[ChatCompletionChoice] = []
    usage: Usage = Field(default_factory=Usage)


@router.post("/chat/completions")
async def chat_completions(request: Request, body: ChatCompletionRequest):
    """OpenAI-compatible chat completions endpoint."""
    from fastapi.responses import JSONResponse
    from mycellm.router.model_resolver import ModelResolver

    node = request.app.state.node
    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    if body.stream:
        return await _stream_response(node, body, messages)

    # Use ModelResolver when model is empty or not found locally
    model_name = node.inference.resolve_model_name(body.model) if body.model else ""
    routed_to = ""

    if not model_name and node.model_resolver:
        resolved = node.model_resolver.resolve(
            body.model,
            node.inference.loaded_models,
            fleet_registry=node.node_registry,
        )
        if resolved:
            best = resolved[0]
            if best.source == "local":
                model_name = best.model_name
            elif best.source == "quic":
                # Route via QUIC peer
                result = await node.route_inference(
                    best.model_name, messages,
                    temperature=body.temperature,
                    max_tokens=body.max_tokens or 2048,
                )
                if result:
                    text = result.get("text", "") if isinstance(result, dict) else result.text
                    routed_to = f"quic:{best.peer_id[:8]}"
                    resp_data = ChatCompletionResponse(
                        model=best.model_name,
                        choices=[
                            ChatCompletionChoice(
                                message=ChatMessage(role="assistant", content=text),
                            )
                        ],
                    )
                    response = JSONResponse(content=resp_data.model_dump())
                    response.headers["X-Mycellm-Routed-To"] = routed_to
                    return response
            elif best.source == "fleet":
                # Route via fleet HTTP
                fleet_result = await _route_via_fleet(
                    node, body, messages, target_model=best.model_name
                )
                if fleet_result:
                    return fleet_result

    # Try local inference
    if not model_name:
        model_name = node.inference.resolve_model_name(body.model)

    if model_name:
        from mycellm.inference.base import InferenceRequest

        req = InferenceRequest(
            messages=messages,
            model=model_name,
            temperature=body.temperature,
            max_tokens=body.max_tokens or 2048,
            top_p=body.top_p,
        )
        result = await node.inference.generate(req)
        resp_data = ChatCompletionResponse(
            model=model_name,
            choices=[
                ChatCompletionChoice(
                    message=ChatMessage(role="assistant", content=result.text),
                    finish_reason=result.finish_reason,
                )
            ],
            usage=Usage(
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.prompt_tokens + result.completion_tokens,
            ),
        )
        response = JSONResponse(content=resp_data.model_dump())
        response.headers["X-Mycellm-Routed-To"] = "local"
        return response

    # Try routing to a remote peer (QUIC)
    result = await node.route_inference(
        body.model, messages,
        temperature=body.temperature,
        max_tokens=body.max_tokens or 2048,
    )
    if result:
        text = result.get("text", "") if isinstance(result, dict) else result.text
        resp_data = ChatCompletionResponse(
            model=body.model or "remote",
            choices=[
                ChatCompletionChoice(
                    message=ChatMessage(role="assistant", content=text),
                )
            ],
        )
        response = JSONResponse(content=resp_data.model_dump())
        response.headers["X-Mycellm-Routed-To"] = "quic:peer"
        return response

    # Try routing via HTTP to fleet nodes (registry-based)
    fleet_result = await _route_via_fleet(node, body, messages)
    if fleet_result:
        return fleet_result

    # No model available — descriptive error
    error_detail = "No model available on the network."
    if body.model:
        error_detail = f"Model '{body.model}' not found. No local, peer, or fleet nodes serve this model."
    else:
        error_detail = "No models loaded locally and no peers or fleet nodes available."

    return ChatCompletionResponse(
        model=body.model or "none",
        choices=[
            ChatCompletionChoice(
                message=ChatMessage(
                    role="assistant",
                    content=f"[mycellm] {error_detail} Load a model with POST /v1/node/models/load or connect to peers.",
                ),
            )
        ],
    )


async def _stream_response(node, body: ChatCompletionRequest, messages: list[dict]):
    """Stream response via SSE."""
    from sse_starlette.sse import EventSourceResponse

    async def generate():
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        model_name = node.inference.resolve_model_name(body.model)

        if model_name:
            from mycellm.inference.base import InferenceRequest

            req = InferenceRequest(
                messages=messages,
                model=model_name,
                temperature=body.temperature,
                max_tokens=body.max_tokens or 2048,
                top_p=body.top_p,
            )

            # Send role delta first
            yield json.dumps({
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model_name,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            })

            async for chunk in node.inference.generate_stream(req):
                if chunk.text:
                    yield json.dumps({
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model_name,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": chunk.text},
                            "finish_reason": chunk.finish_reason,
                        }],
                    })

            # Final chunk
            yield json.dumps({
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model_name,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            })
        else:
            yield json.dumps({
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": body.model or "none",
                "choices": [{
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": "[mycellm] No model available.",
                    },
                    "finish_reason": "stop",
                }],
            })

        yield "[DONE]"

    return EventSourceResponse(generate())


async def _route_via_fleet(
    node, body: ChatCompletionRequest, messages: list[dict], target_model: str = ""
):
    """Route inference to a fleet node via HTTP (registry-based).

    Collects ALL matching nodes, sorts by health, tries in order (failover).
    """
    import httpx
    import logging
    from fastapi.responses import JSONResponse

    logger = logging.getLogger("mycellm.router")

    model_to_route = target_model or body.model

    # Collect and sort matching fleet nodes by health (failure_count ascending)
    matching_entries = []
    for entry in node.node_registry.values():
        if entry.get("status") != "approved" or not entry.get("api_addr"):
            continue
        caps = entry.get("capabilities", {})
        fleet_models = [m.get("name", m) if isinstance(m, dict) else m for m in caps.get("models", [])]
        if model_to_route and model_to_route not in fleet_models:
            continue
        matching_entries.append(entry)

    # Sort by failure count (lower is better), then by node name for stability
    matching_entries.sort(key=lambda e: (e.get("failure_count", 0), e.get("node_name", "")))

    for entry in matching_entries:
        addr = entry["api_addr"]
        base = f"http://{addr}" if not addr.startswith("http") else addr
        url = f"{base}/v1/chat/completions"

        try:
            headers = {"Content-Type": "application/json"}
            from mycellm.config import get_settings
            settings = get_settings()
            if settings.api_key:
                headers["Authorization"] = f"Bearer {settings.api_key}"

            payload = {
                "model": model_to_route or body.model,
                "messages": messages,
                "temperature": body.temperature,
                "max_tokens": body.max_tokens or 2048,
                "top_p": body.top_p,
            }

            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    usage = data.get("usage", {})
                    node_name = entry.get("node_name", addr)
                    logger.info(f"Routed '{model_to_route or body.model}' to fleet node {node_name}")

                    # Reset failure count on success
                    entry["failure_count"] = 0

                    # Debit consumer credits
                    if node.ledger:
                        tokens = usage.get("completion_tokens", 0)
                        from mycellm.accounting.pricing import compute_cost
                        cost = compute_cost(max(tokens, 1))
                        await node.ledger.debit(
                            node.peer_id, cost, "inference_consumed",
                            counterparty_id=entry.get("peer_id", ""),
                        )

                    routed_to = f"fleet:{node_name}"
                    resp_data = ChatCompletionResponse(
                        model=data.get("model", model_to_route or body.model),
                        choices=[
                            ChatCompletionChoice(
                                message=ChatMessage(role="assistant", content=text),
                                finish_reason=data.get("choices", [{}])[0].get("finish_reason", "stop"),
                            )
                        ],
                        usage=Usage(
                            prompt_tokens=usage.get("prompt_tokens", 0),
                            completion_tokens=usage.get("completion_tokens", 0),
                            total_tokens=usage.get("total_tokens", 0),
                        ),
                    )
                    response = JSONResponse(content=resp_data.model_dump())
                    response.headers["X-Mycellm-Routed-To"] = routed_to
                    return response
                else:
                    # Non-200 response, increment failure and try next
                    entry["failure_count"] = entry.get("failure_count", 0) + 1
                    logger.debug(f"Fleet node {addr} returned {resp.status_code}, trying next")
        except Exception as e:
            entry["failure_count"] = entry.get("failure_count", 0) + 1
            logger.debug(f"Fleet route to {addr} failed: {e}")
            continue

    return None


@router.get("/models")
async def list_models(request: Request):
    """List available models (local + remote via QUIC + fleet via registry)."""
    node = request.app.state.node
    models = []

    # Local models
    for m in node.inference.loaded_models:
        models.append({
            "id": m.name,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "local",
        })

    seen = {m.name for m in node.inference.loaded_models}

    # Remote models from QUIC-connected peers
    for entry in node.registry.connected_peers():
        for m in entry.capabilities.models:
            if m.name not in seen:
                models.append({
                    "id": m.name,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": f"peer:{entry.peer_id[:8]}",
                })
                seen.add(m.name)

    # Fleet models from registry (HTTP-routable)
    for entry in node.node_registry.values():
        if entry.get("status") != "approved":
            continue
        caps = entry.get("capabilities", {})
        for m in caps.get("models", []):
            name = m.get("name", m) if isinstance(m, dict) else m
            if name not in seen:
                models.append({
                    "id": name,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": f"fleet:{entry.get('node_name', entry.get('peer_id', '')[:8])}",
                })
                seen.add(name)

    return {"object": "list", "data": models}
