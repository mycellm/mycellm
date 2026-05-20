"""Ollama-compatible API endpoints.

Translates Ollama-native API calls (/api/tags, /api/show, /api/chat)
into mycellm's OpenAI-compatible internals. This allows Ollama client
libraries (used by OpenClaw, etc.) to work against mycellm without
configuration changes.
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("mycellm.api.ollama")

ollama_router = APIRouter()


@ollama_router.get("/tags")
async def list_tags(request: Request):
    """Ollama-compatible model list (GET /api/tags)."""
    node = request.app.state.node
    models = []

    # Virtual "auto" model
    models.append({
        "name": "auto",
        "model": "auto",
        "modified_at": "2026-01-01T00:00:00Z",
        "size": 0,
        "digest": "",
        "details": {
            "parent_model": "",
            "format": "auto",
            "family": "mycellm",
            "parameter_size": "auto",
            "quantization_level": "",
        },
    })

    # Local models
    for m in node.inference.loaded_models:
        models.append({
            "name": m.name,
            "model": m.name,
            "modified_at": "2026-01-01T00:00:00Z",
            "size": int((m.param_count_b or 0) * 1e9),
            "digest": "",
            "details": {
                "parent_model": "",
                "format": "gguf",
                "family": m.backend or "unknown",
                "parameter_size": f"{m.param_count_b or 0}B",
                "quantization_level": m.quant or "",
            },
        })

    # Remote models (QUIC peers + fleet)
    seen = {m.name for m in node.inference.loaded_models}
    seen.add("auto")
    for entry in node.registry.connected_peers():
        for m in entry.capabilities.models:
            if m.name not in seen:
                models.append({
                    "name": m.name,
                    "model": m.name,
                    "modified_at": "2026-01-01T00:00:00Z",
                    "size": int((m.param_count_b or 0) * 1e9),
                    "digest": "",
                    "details": {
                        "parent_model": "",
                        "format": "gguf",
                        "family": m.backend or "unknown",
                        "parameter_size": f"{m.param_count_b or 0}B",
                        "quantization_level": m.quant or "",
                    },
                })
                seen.add(m.name)

    return {"models": models}


@ollama_router.post("/show")
async def show_model(request: Request):
    """Ollama-compatible model info (POST /api/show)."""
    node = request.app.state.node
    body = await request.json()
    name = body.get("name", body.get("model", ""))

    if name == "auto":
        return {
            "modelfile": "",
            "parameters": "",
            "template": "",
            "details": {
                "parent_model": "",
                "format": "auto",
                "family": "mycellm",
                "parameter_size": "auto",
                "quantization_level": "",
            },
            "model_info": {},
        }

    # Check local models
    for m in node.inference.loaded_models:
        if m.name == name:
            return {
                "modelfile": "",
                "parameters": "",
                "template": "",
                "details": {
                    "parent_model": "",
                    "format": "gguf",
                    "family": m.backend or "unknown",
                    "parameter_size": f"{m.param_count_b or 0}B",
                    "quantization_level": m.quant or "",
                },
                "model_info": {},
            }

    # Check remote models
    for entry in node.registry.connected_peers():
        for m in entry.capabilities.models:
            if m.name == name:
                return {
                    "modelfile": "",
                    "parameters": "",
                    "template": "",
                    "details": {
                        "parent_model": "",
                        "format": "gguf",
                        "family": m.backend or "unknown",
                        "parameter_size": f"{m.param_count_b or 0}B",
                        "quantization_level": m.quant or "",
                    },
                    "model_info": {},
                }

    return JSONResponse(status_code=404, content={"error": f"model '{name}' not found"})


@ollama_router.post("/chat")
async def chat(request: Request):
    """Ollama-compatible chat (POST /api/chat).

    Translates to OpenAI-compatible chat completions internally.
    """
    from mycellm.api.openai import ChatCompletionRequest, ChatMessage, chat_completions

    body = await request.json()
    model = body.get("model", "auto")
    messages = [
        ChatMessage(role=m.get("role", "user"), content=m.get("content", ""))
        for m in body.get("messages", [])
    ]
    stream = body.get("stream", False)

    # Build an OpenAI-compatible request and delegate
    oai_body = ChatCompletionRequest(
        model=model,
        messages=messages,
        temperature=body.get("options", {}).get("temperature", 0.7),
        max_tokens=body.get("options", {}).get("num_predict"),
        stream=stream,
        top_p=body.get("options", {}).get("top_p", 1.0),
        seed=body.get("options", {}).get("seed"),
    )

    result = await chat_completions(request, oai_body)

    # If streaming, pass through the SSE response
    if stream:
        return result

    # Translate OpenAI response to Ollama format
    if hasattr(result, 'body'):
        data = json.loads(result.body.decode())
    else:
        data = result

    content = ""
    if data.get("choices"):
        content = data["choices"][0].get("message", {}).get("content", "")

    return {
        "model": data.get("model", model),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "message": {
            "role": "assistant",
            "content": content,
        },
        "done": True,
        "total_duration": 0,
        "load_duration": 0,
        "prompt_eval_count": data.get("usage", {}).get("prompt_tokens", 0),
        "prompt_eval_duration": 0,
        "eval_count": data.get("usage", {}).get("completion_tokens", 0),
        "eval_duration": 0,
    }
