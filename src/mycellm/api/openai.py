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
    node = request.app.state.node
    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    if body.stream:
        return await _stream_response(node, body, messages)

    # Try local inference first
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
        return ChatCompletionResponse(
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

    # Try routing to a remote peer (QUIC)
    result = await node.route_inference(
        body.model, messages,
        temperature=body.temperature,
        max_tokens=body.max_tokens or 2048,
    )
    if result:
        text = result.get("text", "") if isinstance(result, dict) else result.text
        return ChatCompletionResponse(
            model=body.model or "remote",
            choices=[
                ChatCompletionChoice(
                    message=ChatMessage(role="assistant", content=text),
                )
            ],
        )

    # Try routing via HTTP to fleet nodes (registry-based)
    fleet_result = await _route_via_fleet(node, body, messages)
    if fleet_result:
        return fleet_result

    # No model available
    return ChatCompletionResponse(
        model=body.model or "none",
        choices=[
            ChatCompletionChoice(
                message=ChatMessage(
                    role="assistant",
                    content="[mycellm] No model available. Load a model or connect to peers.",
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


async def _route_via_fleet(node, body: ChatCompletionRequest, messages: list[dict]):
    """Route inference to a fleet node via HTTP (registry-based)."""
    import httpx
    import logging

    logger = logging.getLogger("mycellm.router")

    for entry in node.node_registry.values():
        if entry.get("status") != "approved" or not entry.get("api_addr"):
            continue
        # Check if this node has the requested model
        caps = entry.get("capabilities", {})
        fleet_models = [m.get("name", m) if isinstance(m, dict) else m for m in caps.get("models", [])]
        if body.model and body.model not in fleet_models:
            continue

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
                "model": body.model,
                "messages": messages,
                "temperature": body.temperature,
                "max_tokens": body.max_tokens or 2048,
                "top_p": body.top_p,
            }

            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    usage = data.get("usage", {})
                    logger.info(f"Routed '{body.model}' to fleet node {entry.get('node_name', addr)}")

                    # Debit consumer credits
                    if node.ledger:
                        tokens = usage.get("completion_tokens", 0)
                        from mycellm.accounting.pricing import compute_cost
                        cost = compute_cost(max(tokens, 1))
                        await node.ledger.debit(
                            node.peer_id, cost, "inference_consumed",
                            counterparty_id=entry.get("peer_id", ""),
                        )

                    return ChatCompletionResponse(
                        model=data.get("model", body.model),
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
        except Exception as e:
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
