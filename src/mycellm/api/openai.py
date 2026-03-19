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

    # Try routing to a remote peer
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


@router.get("/models")
async def list_models(request: Request):
    """List available models (local + remote)."""
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

    # Remote models from connected peers
    seen = {m.name for m in node.inference.loaded_models}
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

    return {"object": "list", "data": models}
