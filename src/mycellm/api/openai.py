"""OpenAI-compatible chat completions API."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Optional

logger = logging.getLogger("mycellm.api")

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class MycellmRouting(BaseModel):
    min_tier: str = ""          # "frontier", "capable", "fast", "tiny"
    min_params: float = 0       # minimum param count in billions
    min_context: int = 0        # minimum context window
    required_tags: list[str] = []  # must have these tags
    max_cost: float = 0         # max credits per request (0 = unlimited)
    routing: str = "best"       # "best", "fastest", "ensemble"
    fallback: str = "downgrade" # "reject" or "downgrade"
    trust: str = ""             # "local", "trusted", "any" — route only to peers at this trust level or higher


class ChatCompletionRequest(BaseModel):
    model: str = ""
    messages: list[ChatMessage]
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    stream: bool = False
    top_p: float = 1.0
    stop: list[str] | str | None = None
    frequency_penalty: float = 0
    presence_penalty: float = 0
    seed: int | None = None
    response_format: dict | None = None  # {"type": "json_object"}
    mycellm: MycellmRouting | None = None


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
    from mycellm.activity import EventType

    node = request.app.state.node
    messages = [{"role": m.role, "content": m.content} for m in body.messages]
    start_time = time.time()
    node.activity.record(EventType.INFERENCE_START, model=body.model, source="api")

    if body.stream:
        return await _stream_response(node, body, messages)

    # Use ModelResolver when model is empty or not found locally
    model_name = node.inference.resolve_model_name(body.model) if body.model else ""
    routed_to = ""

    # Build quality constraints from mycellm routing params
    constraints = None
    if body.mycellm:
        from mycellm.router.model_resolver import QualityConstraints
        constraints = QualityConstraints(
            min_tier=body.mycellm.min_tier,
            min_params=body.mycellm.min_params,
            min_context=body.mycellm.min_context,
            required_tags=body.mycellm.required_tags,
            max_cost=body.mycellm.max_cost,
            trust=body.mycellm.trust,
        )

    if not model_name and node.model_resolver:
        # Get consumer balance for priority routing
        _balance = -1.0  # -1 = no restriction (default)
        if node.ledger:
            _balance = await node.ledger.balance(node.peer_id)
        resolved = node.model_resolver.resolve(
            body.model,
            node.inference.loaded_models,
            fleet_registry=node.node_registry,
            constraints=constraints,
            consumer_balance=_balance,
        )
        if not resolved and constraints and body.mycellm:
            if body.mycellm.fallback == "reject":
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": {
                            "message": "No models match the requested quality constraints.",
                            "type": "quality_constraint_error",
                            "code": "no_matching_model",
                        }
                    },
                )
            elif body.mycellm.fallback == "downgrade":
                # Retry without constraints
                resolved = node.model_resolver.resolve(
                    body.model,
                    node.inference.loaded_models,
                    fleet_registry=node.node_registry,
                )
                # We'll add a warning header below

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
                    prompt_tokens = result.get("prompt_tokens", 0) if isinstance(result, dict) else 0
                    completion_tokens = result.get("completion_tokens", 0) if isinstance(result, dict) else 0
                    total_tokens = prompt_tokens + completion_tokens
                    routed_to = f"quic:{best.peer_id[:8]}"
                    node.activity.record(
                        EventType.INFERENCE_COMPLETE,
                        model=best.model_name,
                        source="quic",
                        routed_to=routed_to,
                        tokens=total_tokens,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        latency_ms=round((time.time() - start_time) * 1000),
                    )
                    resp_data = ChatCompletionResponse(
                        model=best.model_name,
                        choices=[
                            ChatCompletionChoice(
                                message=ChatMessage(role="assistant", content=text),
                            )
                        ],
                        usage=Usage(
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=total_tokens,
                        ),
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

        # Normalize stop to list[str] | None
        stop = body.stop
        if isinstance(stop, str):
            stop = [stop]

        req = InferenceRequest(
            messages=messages,
            model=model_name,
            temperature=body.temperature,
            max_tokens=body.max_tokens or 2048,
            top_p=body.top_p,
            stop=stop,
            frequency_penalty=body.frequency_penalty,
            presence_penalty=body.presence_penalty,
            seed=body.seed,
            response_format=body.response_format,
        )
        try:
            result = await node.inference.generate(req)
        except Exception as e:
            node.activity.record(EventType.INFERENCE_FAILED, model=model_name, error=str(e)[:200])
            error_msg = str(e)
            if "401" in error_msg or "Unauthorized" in error_msg:
                error_msg = f"API key rejected by upstream provider for model '{model_name}'. Check your API key."
            elif "ConnectError" in error_msg or "connect" in error_msg.lower():
                error_msg = f"Cannot reach backend for model '{model_name}'. Is the API endpoint available?"
            return ChatCompletionResponse(
                model=model_name,
                choices=[ChatCompletionChoice(
                    message=ChatMessage(role="assistant", content=f"[mycellm] Inference error: {error_msg}"),
                    finish_reason="error",
                )],
            )
        node.activity.record(
            EventType.INFERENCE_COMPLETE,
            model=model_name,
            source="local",
            tokens=result.prompt_tokens + result.completion_tokens,
            latency_ms=round((time.time() - start_time) * 1000),
        )
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
        node.activity.record(
            EventType.INFERENCE_COMPLETE,
            model=body.model,
            source="quic",
            routed_to="quic:peer",
            latency_ms=round((time.time() - start_time) * 1000),
        )
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
    node.activity.record(EventType.INFERENCE_FAILED, model=body.model, error="no_model_available")
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

            # Normalize stop to list[str] | None
            stop = body.stop
            if isinstance(stop, str):
                stop = [stop]

            req = InferenceRequest(
                messages=messages,
                model=model_name,
                temperature=body.temperature,
                max_tokens=body.max_tokens or 2048,
                top_p=body.top_p,
                stop=stop,
                frequency_penalty=body.frequency_penalty,
                presence_penalty=body.presence_penalty,
                seed=body.seed,
                response_format=body.response_format,
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
            # Try fleet streaming
            import httpx
            fleet_handled = False
            for entry in node.node_registry.values():
                if entry.get("status") != "approved" or not entry.get("api_addr"):
                    continue
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
                        "stream": True,
                    }

                    async with httpx.AsyncClient(timeout=120) as client:
                        async with client.stream("POST", url, json=payload, headers=headers) as resp:
                            if resp.status_code == 200:
                                fleet_handled = True
                                async for line in resp.aiter_lines():
                                    if line.startswith("data: "):
                                        data = line[6:]
                                        if data == "[DONE]":
                                            yield "[DONE]"
                                            return
                                        yield data
                                return
                except Exception as e:
                    logging.getLogger("mycellm.router").debug(f"Fleet stream to {addr} failed: {e}")
                    continue

            if not fleet_handled:
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

            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    usage = data.get("usage", {})
                    node_name = entry.get("node_name", addr)
                    logger.info(f"Routed '{model_to_route or body.model}' to fleet node {node_name}")

                    # Reset failure count on success
                    entry["failure_count"] = 0

                    # Debit consumer credits + store receipt
                    if node.ledger:
                        tokens = usage.get("completion_tokens", 0)
                        from mycellm.accounting.pricing import compute_cost
                        cost = compute_cost(max(tokens, 1))
                        seeder_peer = entry.get("peer_id", "")
                        try:
                            tx_id = await node.ledger.debit(
                                node.peer_id, cost, "inference_consumed",
                                counterparty_id=seeder_peer,
                            )
                            # Store a fleet receipt (unsigned — HTTP, not QUIC)
                            await node.ledger.store_receipt(
                                tx_id=tx_id,
                                consumer_id=node.peer_id,
                                seeder_id=seeder_peer,
                                model=model_to_route or body.model,
                                tokens=tokens,
                                cost=cost,
                                signature="fleet",  # marker: fleet receipt, not Ed25519 signed
                            )
                        except ValueError as e:
                            logger.warning(f"Credit debit failed: {e}")
                        from mycellm.activity import EventType as _ET
                        node.activity.record(_ET.CREDIT_SPENT, amount=cost, reason="inference_consumed")

                    routed_to = f"fleet:{node_name}"
                    from mycellm.activity import EventType as _ET
                    node.activity.record(
                        _ET.INFERENCE_COMPLETE,
                        model=model_to_route or body.model,
                        source="fleet",
                        routed_to=routed_to,
                        tokens=usage.get("total_tokens", 0),
                    )
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


@router.post("/embeddings")
async def create_embeddings(request: Request):
    """OpenAI-compatible embeddings endpoint."""
    from fastapi.responses import JSONResponse

    node = request.app.state.node
    body = await request.json()
    model = body.get("model", "")
    input_text = body.get("input", "")

    model_name = node.inference.resolve_model_name(model)
    if not model_name:
        return JSONResponse(status_code=400, content={"error": {"message": "No model available for embeddings"}})

    backend = node.inference.get_backend(model_name)
    if not backend:
        return JSONResponse(status_code=400, content={"error": {"message": f"Model '{model_name}' not found"}})

    try:
        from mycellm.inference.base import EmbeddingRequest
        req = EmbeddingRequest(input=input_text, model=model_name)
        result = await backend.embed(req)

        data = []
        for i, emb in enumerate(result.embeddings):
            data.append({"object": "embedding", "index": i, "embedding": emb})

        return {
            "object": "list",
            "data": data,
            "model": model_name,
            "usage": {"prompt_tokens": result.total_tokens, "total_tokens": result.total_tokens},
        }
    except NotImplementedError:
        return JSONResponse(status_code=400, content={"error": {"message": f"Model '{model_name}' doesn't support embeddings"}})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": {"message": str(e)}})
