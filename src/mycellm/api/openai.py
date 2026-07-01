"""OpenAI-compatible chat completions API."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger("mycellm.api")


_TOOLCALL_PATTERNS = (
    # Qwen2.5-Instruct style: <tool_call>{"name":..., "arguments":...}</tool_call>
    re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL),
    # Qwen-Coder MLX style: ```json\n{"name":..., "arguments":...}\n```
    re.compile(r"```(?:json|tool_call)?\s*\n?\s*(\{[^`]*?\"name\"[^`]*?\"arguments\"[^`]*?\})\s*\n?\s*```", re.DOTALL),
)


def _parse_tool_call_xml(text: str) -> list | None:
    """Convert free-text tool calls into OpenAI tool_calls format.

    Models that don't reliably emit native function_calling JSON often produce
    tool calls in surrounding markup. We recognise:
      - Qwen-Instruct: <tool_call>{...}</tool_call>
      - Qwen-Coder MLX: ```json\\n{...}\\n```  (bare JSON inside a code fence)

    Returned dicts follow OpenAI spec — function.arguments is a JSON string.
    """
    tool_calls: list[dict] = []
    seen_payloads: set[str] = set()
    for pattern in _TOOLCALL_PATTERNS:
        for match in pattern.finditer(text):
            payload = match.group(1)
            if payload in seen_payloads:
                continue
            try:
                data = json.loads(payload)
            except (json.JSONDecodeError, AttributeError):
                continue
            if not isinstance(data, dict) or "name" not in data:
                continue
            seen_payloads.add(payload)
            arguments = data.get("arguments", {})
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments)
            elif not isinstance(arguments, str):
                arguments = json.dumps(arguments)
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:16]}",
                "type": "function",
                "function": {"name": data["name"], "arguments": arguments},
            })
    return tool_calls if tool_calls else None


def _resolve_reasoning_exclude(body_reasoning: dict | None) -> bool:
    """Decide whether to suppress reasoning for this request.

    Explicit body.reasoning.exclude wins. Otherwise fall back to the
    MYCELLM_HIDE_REASONING_BY_DEFAULT setting (public bootstraps default
    to true; self-hosted nodes default to false).
    """
    if body_reasoning is not None and "exclude" in body_reasoning:
        return bool(body_reasoning["exclude"])
    try:
        from mycellm.config import get_settings
        return bool(get_settings().hide_reasoning_by_default)
    except Exception:
        return False


def _split_text_for_message(text: str, model_name: str, reasoning_exclude: bool) -> tuple[str, str | None]:
    """Split raw model output into (content, reasoning_content_or_None).

    When reasoning_exclude is True we still split (so <think> blocks don't leak
    into content) but we drop the reasoning side (returns None for it). When
    False we surface reasoning_content as a separate field so the client can
    render it in a collapsible panel.
    """
    from mycellm.inference.reasoning_dialects import split_reasoning
    content, reasoning = split_reasoning(text or "", model_name)
    if reasoning_exclude:
        return content, None
    return content, (reasoning or None)


def _find_alternative_model(node, busy_model: str) -> str | None:
    """Find another loaded model to use when the requested one is busy."""
    for m in node.inference.loaded_models:
        if m.name != busy_model:
            # Check if the model's lock is free (not queued)
            lock = node.inference._model_locks.get(m.name)
            if lock and isinstance(lock, __import__('asyncio').Lock) and lock.locked():
                continue  # this one is also busy
            return m.name
    return None

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    # content is a plain string for text, or a list of OpenAI content parts for
    # multimodal input — [{"type":"text",...}, {"type":"image_url",...}]. It
    # flows through to the backend as-is: text backends flatten it to text
    # (base.flatten_message_content), the mlx-vlm backend splits out images.
    content: Optional[str | list[dict]] = None
    tool_calls: Optional[list] = None      # assistant → tool invocations
    tool_call_id: Optional[str] = None     # tool → result for this call_id
    name: Optional[str] = None             # tool → function name
    reasoning_content: Optional[str] = None  # extracted <think>...</think>, OpenAI-o1 style


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
    # OpenAI renamed max_tokens -> max_completion_tokens for chat completions.
    # Accept it as an alias so newer SDK clients keep working (folded into
    # max_tokens by the validator below, so all downstream reads are unchanged).
    max_completion_tokens: Optional[int] = None
    stream: bool = False
    top_p: float = 1.0
    stop: list[str] | str | None = None
    frequency_penalty: float = 0
    presence_penalty: float = 0
    seed: int | None = None
    response_format: dict | None = None  # {"type": "json_object"}
    grammar: str | None = None  # GBNF grammar for constrained output (llama.cpp)
    tools: list | None = None              # OpenAI tool definitions
    tool_choice: str | dict | None = None  # "auto", "none", "required", or specific tool
    # OpenAI-o-series style reasoning control. Recognised shapes:
    #   {"exclude": true}   strip thinking from output (and ask backend to suppress)
    #   {"exclude": false}  include thinking
    #   {"effort": "low"|"medium"|"high"}  passed through for reasoning-API backends
    # Omitted → server default (MYCELLM_HIDE_REASONING_BY_DEFAULT decides).
    reasoning: dict | None = None
    mycellm: MycellmRouting | None = None

    @model_validator(mode="after")
    def _fold_max_completion_tokens(self) -> "ChatCompletionRequest":
        # max_tokens takes precedence if a client sends both.
        if self.max_tokens is None and self.max_completion_tokens is not None:
            self.max_tokens = self.max_completion_tokens
        return self


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
    from mycellm.activity import EventType

    node = request.app.state.node
    messages = []
    for m in body.messages:
        msg: dict = {"role": m.role}
        if m.content is not None:
            msg["content"] = m.content
        if m.tool_calls is not None:
            msg["tool_calls"] = m.tool_calls
        if m.tool_call_id is not None:
            msg["tool_call_id"] = m.tool_call_id
        if m.name is not None:
            msg["name"] = m.name
        messages.append(msg)
    reasoning_exclude = _resolve_reasoning_exclude(body.reasoning)
    start_time = time.time()
    node.activity.record(EventType.INFERENCE_START, model=body.model, source="api")

    if body.stream:
        return await _stream_response(node, body, messages)

    # Use ModelResolver when model is empty, "auto", or not found locally
    requested_model = body.model if body.model != "auto" else ""
    model_name = node.inference.resolve_model_name(requested_model) if requested_model else ""
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
            requested_model,
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
                    requested_model,
                    node.inference.loaded_models,
                    fleet_registry=node.node_registry,
                )
                # We'll add a warning header below

        # Try each ranked candidate in turn — if the best model's seeder is
        # down, fall through to the next-best rather than failing the request.
        for best in (resolved or []):
            if best.source == "local":
                model_name = best.model_name
                break  # handled by the local-inference path below
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
                    # Apply reasoning split locally: peers may not have suppressed
                    # thinking (older nodes, or didn't get the reasoning param via
                    # the QUIC envelope). Split <think>...</think> at the bootstrap
                    # so clients always see a clean answer / side-channel split.
                    content_text, reasoning_text = _split_text_for_message(
                        text, best.model_name, reasoning_exclude,
                    )
                    resp_data = ChatCompletionResponse(
                        model=best.model_name,
                        choices=[
                            ChatCompletionChoice(
                                message=ChatMessage(
                                    role="assistant",
                                    content=content_text or None,
                                    reasoning_content=reasoning_text,
                                ),
                            )
                        ],
                        usage=Usage(
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=total_tokens,
                        ),
                    )
                    response = JSONResponse(content=resp_data.model_dump(exclude_none=True))
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
        model_name = node.inference.resolve_model_name(requested_model)

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
            grammar=body.grammar,
            tools=body.tools,
            tool_choice=body.tool_choice,
            reasoning_exclude=reasoning_exclude,
        )
        try:
            result = await node.inference.generate(req)
        except RuntimeError as e:
            if "busy" in str(e).lower() or "timed out" in str(e).lower():
                # Try alternative loaded models before returning 503
                alt_model = _find_alternative_model(node, model_name)
                if alt_model:
                    logger.info(f"Model {model_name} busy, trying {alt_model}")
                    req.model = alt_model
                    try:
                        result = await node.inference.generate(req)
                        model_name = alt_model  # update for response
                    except RuntimeError:
                        return JSONResponse(status_code=503, content={
                            "error": {"message": str(e), "type": "model_busy", "code": "model_busy"}
                        })
                else:
                    return JSONResponse(status_code=503, content={
                        "error": {"message": str(e), "type": "model_busy", "code": "model_busy"}
                    })
            else:
                raise
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
        # Some backends emit tool calls as <tool_call> XML text (e.g. Qwen via
        # llama-cpp-python with tool_choice "auto" or unset).  Parse and normalize
        # to standard tool_calls JSON so callers always see a consistent format.
        if req.tools and not result.tool_calls and result.text and ("<tool_call>" in result.text or '"name"' in result.text):
            parsed = _parse_tool_call_xml(result.text)
            if parsed:
                result.tool_calls = parsed
                result.text = ""
                result.finish_reason = "tool_calls"

        node.activity.record(
            EventType.INFERENCE_COMPLETE,
            model=model_name,
            source="local",
            tokens=result.prompt_tokens + result.completion_tokens,
            latency_ms=round((time.time() - start_time) * 1000),
        )
        content_text, reasoning_text = _split_text_for_message(
            result.text or "", model_name, reasoning_exclude,
        )
        resp_data = ChatCompletionResponse(
            model=model_name,
            choices=[
                ChatCompletionChoice(
                    message=ChatMessage(
                        role="assistant",
                        content=content_text or None,
                        tool_calls=result.tool_calls,
                        reasoning_content=reasoning_text,
                    ),
                    finish_reason=result.finish_reason,
                )
            ],
            usage=Usage(
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.prompt_tokens + result.completion_tokens,
            ),
        )
        response = JSONResponse(content=resp_data.model_dump(exclude_none=True))
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
        content_text, reasoning_text = _split_text_for_message(
            text, body.model or "", reasoning_exclude,
        )
        resp_data = ChatCompletionResponse(
            model=body.model or "remote",
            choices=[
                ChatCompletionChoice(
                    message=ChatMessage(
                        role="assistant",
                        content=content_text or None,
                        reasoning_content=reasoning_text,
                    ),
                )
            ],
        )
        response = JSONResponse(content=resp_data.model_dump(exclude_none=True))
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
    from mycellm.inference.reasoning_dialects import make_splitter

    reasoning_exclude = _resolve_reasoning_exclude(body.reasoning)

    async def generate():
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        _requested = body.model if body.model not in ("auto", "") else ""

        # For auto routing, use ModelResolver for quality-based selection
        # rather than first-loaded model (which could be a tiny model).
        model_name = ""
        if not _requested and node.model_resolver:
            resolved = node.model_resolver.resolve(
                "",
                node.inference.loaded_models,
                fleet_registry=node.node_registry,
            )
            if resolved:
                best = resolved[0]
                if best.source in ("local", "fleet"):
                    model_name = best.model_name
                # quic-only: fall through to the no-local-model path below
        if not model_name:
            model_name = node.inference.resolve_model_name(_requested)

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
                grammar=body.grammar,
                tools=body.tools,
                tool_choice=body.tool_choice,
                reasoning_exclude=reasoning_exclude,
            )

            # Per-stream splitter routes <think>-block tokens to
            # delta.reasoning_content and post-think tokens to delta.content.
            # No-op (passthrough as content) for models without an output_tag_pair.
            splitter = make_splitter(model_name)

            # When tools are requested, streaming tool_call deltas are unreliable
            # (llama-cpp-python and many backends don't emit them).  Fall back to
            # a single non-streaming generate() call and emit the result as SSE.
            # Also: "auto" tool_choice causes some backends to return tool calls
            # as <tool_call> XML text instead of proper JSON tool_calls.  When
            # exactly one tool is defined and choice is "auto"/"none"/unset, force
            # it to {"type":"function","function":{"name":<tool>}} which reliably
            # produces the standard tool_calls response format.
            if req.tools:
                if req.tool_choice in (None, "auto", "none") and len(req.tools) == 1:
                    try:
                        fn_name = req.tools[0]["function"]["name"]
                        req.tool_choice = {"type": "function", "function": {"name": fn_name}}
                    except (KeyError, TypeError, IndexError):
                        pass
                result = await node.inference.generate(req)

                # Normalize <tool_call> XML text → tool_calls JSON (Qwen multi-tool case)
                if not result.tool_calls and result.text and ("<tool_call>" in result.text or '"name"' in result.text):
                    parsed = _parse_tool_call_xml(result.text)
                    if parsed:
                        result.tool_calls = parsed
                        result.text = ""
                        result.finish_reason = "tool_calls"

                yield json.dumps({
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model_name,
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                })
                if result.tool_calls:
                    yield json.dumps({
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model_name,
                        "choices": [{
                            "index": 0,
                            "delta": {"tool_calls": result.tool_calls},
                            "finish_reason": "tool_calls",
                        }],
                    })
                elif result.text:
                    yield json.dumps({
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model_name,
                        "choices": [{"index": 0, "delta": {"content": result.text}, "finish_reason": None}],
                    })
                yield json.dumps({
                    "id": chunk_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model_name,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": result.finish_reason}],
                })
                return

            # Send role delta first
            yield json.dumps({
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model_name,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            })

            got_tool_calls = False

            def _envelope(delta: dict, finish: str | None = None) -> str:
                return json.dumps({
                    "id": chunk_id, "object": "chat.completion.chunk",
                    "created": int(time.time()), "model": model_name,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                })

            def _emit_chunk(chunk_val):
                """Feed a streaming InferenceChunk through the think-splitter and
                yield SSE envelopes. May produce 0, 1, or multiple envelopes per
                input chunk (one input token can straddle a <think> boundary)."""
                nonlocal got_tool_calls
                if chunk_val.tool_calls:
                    got_tool_calls = True
                    yield _envelope({"tool_calls": chunk_val.tool_calls}, finish="tool_calls")
                    return
                if not chunk_val.text:
                    return
                for kind, piece in splitter.feed(chunk_val.text):
                    if kind == "content":
                        yield _envelope({"content": piece})
                    elif kind == "reasoning" and not reasoning_exclude:
                        # OpenAI o-series convention: delta.reasoning_content
                        yield _envelope({"reasoning_content": piece})
                    # else: kind=="reasoning" and excluded → silently drop

            try:
                stream_iter = node.inference.generate_stream(req)
                # Acquire the lock by getting the first iteration
                first_chunk_val = await stream_iter.__anext__()
                # Got the lock — yield first chunk(s) and continue
                for out in _emit_chunk(first_chunk_val):
                    yield out
            except RuntimeError as busy_err:
                if "busy" in str(busy_err).lower() or "timed out" in str(busy_err).lower():
                    alt = _find_alternative_model(node, model_name)
                    if alt:
                        logger.info(f"Stream: {model_name} busy, falling back to {alt}")
                        req.model = alt
                        model_name = alt
                        stream_iter = node.inference.generate_stream(req)
                    else:
                        yield json.dumps({
                            "id": chunk_id, "object": "chat.completion.chunk",
                            "model": model_name,
                            "choices": [{"index": 0, "delta": {"content": "[Model busy — try again]"}, "finish_reason": "stop"}],
                        })
                        return
                else:
                    raise

            async for chunk in stream_iter:
                for out in _emit_chunk(chunk):
                    yield out

            # Drain the splitter — handles unclosed <think> at end of stream.
            for kind, piece in splitter.flush():
                if kind == "content":
                    yield _envelope({"content": piece})
                elif kind == "reasoning" and not reasoning_exclude:
                    yield _envelope({"reasoning_content": piece})

            # Final chunk — use "tool_calls" finish_reason if model called a tool
            yield _envelope({}, finish=("tool_calls" if got_tool_calls else "stop"))
        else:
            # No local model — try streaming via direct QUIC peers first
            # (this is the canonical P2P path: chain_builder picks the best
            # peer that advertises the requested model in its capabilities).
            fleet_handled = False
            try:
                quic_chunk_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
                role_sent = False
                any_quic_chunk = False
                # Apply the same think-splitter on the relay path: peer may not
                # have suppressed thinking, so split <think> blocks at the
                # bootstrap and route them to delta.reasoning_content.
                quic_splitter = make_splitter(body.model or "")
                async for piece in node.route_inference_stream(
                    body.model, messages,
                    temperature=body.temperature,
                    max_tokens=body.max_tokens or 2048,
                    tools=body.tools or None,
                    tool_choice=body.tool_choice,
                ):
                    if not role_sent:
                        yield json.dumps({
                            "id": quic_chunk_id, "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": body.model or "auto",
                            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                        })
                        role_sent = True
                    text = piece.get("text", "") if isinstance(piece, dict) else getattr(piece, "text", "")
                    finish = piece.get("finish_reason") if isinstance(piece, dict) else None
                    tool_calls = piece.get("tool_calls") if isinstance(piece, dict) else None
                    if tool_calls:
                        any_quic_chunk = True
                        yield json.dumps({
                            "id": quic_chunk_id, "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": body.model or "auto",
                            "choices": [{"index": 0, "delta": {"tool_calls": tool_calls}, "finish_reason": finish}],
                        })
                    elif text:
                        for kind, chunk_piece in quic_splitter.feed(text):
                            if kind == "content":
                                any_quic_chunk = True
                                yield json.dumps({
                                    "id": quic_chunk_id, "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": body.model or "auto",
                                    "choices": [{"index": 0, "delta": {"content": chunk_piece}, "finish_reason": None}],
                                })
                            elif kind == "reasoning" and not reasoning_exclude:
                                any_quic_chunk = True
                                yield json.dumps({
                                    "id": quic_chunk_id, "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": body.model or "auto",
                                    "choices": [{"index": 0, "delta": {"reasoning_content": chunk_piece}, "finish_reason": None}],
                                })
                # Drain splitter (unclosed <think> at end-of-stream)
                for kind, chunk_piece in quic_splitter.flush():
                    if kind == "content":
                        yield json.dumps({
                            "id": quic_chunk_id, "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": body.model or "auto",
                            "choices": [{"index": 0, "delta": {"content": chunk_piece}, "finish_reason": None}],
                        })
                    elif kind == "reasoning" and not reasoning_exclude:
                        yield json.dumps({
                            "id": quic_chunk_id, "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": body.model or "auto",
                            "choices": [{"index": 0, "delta": {"reasoning_content": chunk_piece}, "finish_reason": None}],
                        })
                if any_quic_chunk:
                    yield json.dumps({
                        "id": quic_chunk_id, "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": body.model or "auto",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    })
                    yield "[DONE]"
                    return
            except Exception as e:
                logging.getLogger("mycellm.router").debug(f"QUIC stream routing failed: {e}")

            # Then check fleet/announced seeders (HTTP path or QUIC fallback).
            now = time.time()
            matching_entries = []
            for entry in node.node_registry.values():
                if entry.get("status") != "approved":
                    continue
                if now - entry.get("last_seen", 0) > 120:
                    continue
                caps = entry.get("capabilities", {})
                fleet_models = [m.get("name", m) if isinstance(m, dict) else m for m in caps.get("models", [])]
                if body.model and body.model not in fleet_models:
                    continue
                matching_entries.append(entry)

            # QUIC-first ordering
            def _sk(e):
                pid = e.get("peer_id", "")
                return (0 if (pid and pid in getattr(node, "_peer_connections", {})) else 1,
                        e.get("failure_count", 0))
            matching_entries.sort(key=_sk)

            for entry in matching_entries:
                pid = entry.get("peer_id", "")
                if not pid or pid not in getattr(node, "_peer_connections", {}):
                    continue
                try:
                    chunk_id_q = f"chatcmpl-{uuid.uuid4().hex[:8]}"
                    yield json.dumps({
                        "id": chunk_id_q, "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": body.model or "auto",
                        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                    })
                    any_chunk = False
                    async for piece in node.route_inference_stream(
                        body.model, messages,
                        temperature=body.temperature,
                        max_tokens=body.max_tokens or 2048,
                        tools=body.tools or None,
                        tool_choice=body.tool_choice,
                    ):
                        any_chunk = True
                        text = piece.get("text", "") if isinstance(piece, dict) else getattr(piece, "text", "")
                        finish = piece.get("finish_reason") if isinstance(piece, dict) else None
                        tool_calls = piece.get("tool_calls") if isinstance(piece, dict) else None
                        if text or tool_calls:
                            delta2: dict = {}
                            if text:
                                delta2["content"] = text
                            if tool_calls:
                                delta2["tool_calls"] = tool_calls
                            yield json.dumps({
                                "id": chunk_id_q, "object": "chat.completion.chunk",
                                "created": int(time.time()),
                                "model": body.model or "auto",
                                "choices": [{"index": 0, "delta": delta2, "finish_reason": finish}],
                            })
                    if any_chunk:
                        yield json.dumps({
                            "id": chunk_id_q, "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": body.model or "auto",
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        })
                        fleet_handled = True
                        yield "[DONE]"
                        return
                except Exception as e:
                    logging.getLogger("mycellm.router").debug(
                        f"QUIC stream to {pid[:16]} failed: {e}")
                    entry["failure_count"] = entry.get("failure_count", 0) + 1
                    continue

            # Fallback: try fleet HTTP streaming against api_addr.
            import httpx
            for entry in matching_entries:
                if not entry.get("api_addr"):
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
                    if body.tools:
                        payload["tools"] = body.tools
                    if body.tool_choice is not None:
                        payload["tool_choice"] = body.tool_choice

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
    """Route inference to a fleet node.

    Prefers an already-open QUIC session (works for NAT'd home seeders) and
    falls back to HTTP POST against the seeder's self-reported api_addr
    (works for seeders with a public endpoint). Failover across all matching
    nodes, sorted by health.
    """
    import time
    import httpx
    import logging
    from fastapi.responses import JSONResponse

    logger = logging.getLogger("mycellm.router")

    model_to_route = target_model or body.model

    # Collect and sort matching fleet nodes: freshness first, then health.
    # Skip entries whose last_seen is too stale — a seeder that hasn't
    # checked in for >2min is treated as gone.
    now = time.time()
    matching_entries = []
    for entry in node.node_registry.values():
        if entry.get("status") != "approved":
            continue
        if now - entry.get("last_seen", 0) > 120:
            continue
        caps = entry.get("capabilities", {})
        fleet_models = [m.get("name", m) if isinstance(m, dict) else m for m in caps.get("models", [])]
        if model_to_route and model_to_route not in fleet_models:
            continue
        matching_entries.append(entry)

    # Sort: live QUIC session first, then failure count, then name
    def _sort_key(e):
        peer_id = e.get("peer_id", "")
        has_quic = 0 if (peer_id and peer_id in getattr(node, "_peer_connections", {})) else 1
        return (has_quic, e.get("failure_count", 0), e.get("node_name", ""))
    matching_entries.sort(key=_sort_key)

    # Try QUIC routing first for any entry that has a live peer connection.
    # This is the critical path for home seeders behind NAT: they hold an
    # outbound QUIC session open, and the bootstrap pushes inference down it.
    for entry in matching_entries:
        peer_id = entry.get("peer_id", "")
        if not peer_id or peer_id not in getattr(node, "_peer_connections", {}):
            continue
        try:
            result = await node.route_inference(
                model_to_route or body.model, messages,
                temperature=body.temperature,
                max_tokens=body.max_tokens or 2048,
            )
        except Exception as e:
            logger.debug(f"QUIC route to {peer_id[:16]} failed: {e}")
            entry["failure_count"] = entry.get("failure_count", 0) + 1
            try:
                from mycellm.metrics import bootstrap_routed_total
                bootstrap_routed_total.labels(transport="quic", outcome="fail").inc()
            except Exception:
                pass
            continue
        if not result:
            try:
                from mycellm.metrics import bootstrap_routed_total
                bootstrap_routed_total.labels(transport="quic", outcome="fail").inc()
            except Exception:
                pass
            continue
        text = result.get("text", "") if isinstance(result, dict) else result.text
        prompt_tokens = result.get("prompt_tokens", 0) if isinstance(result, dict) else 0
        completion_tokens = result.get("completion_tokens", 0) if isinstance(result, dict) else 0
        total_tokens = prompt_tokens + completion_tokens
        entry["failure_count"] = 0
        node_name = entry.get("node_name", peer_id[:8])
        routed_to = f"quic:{node_name}"
        from mycellm.activity import EventType as _ET
        node.activity.record(
            _ET.INFERENCE_COMPLETE,
            model=model_to_route or body.model,
            source="quic",
            routed_to=routed_to,
            tokens=total_tokens,
        )
        resp_data = ChatCompletionResponse(
            model=model_to_route or body.model,
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
        try:
            from mycellm.metrics import bootstrap_routed_total
            bootstrap_routed_total.labels(transport="quic", outcome="success").inc()
        except Exception:
            pass
        response = JSONResponse(content=resp_data.model_dump())
        response.headers["X-Mycellm-Routed-To"] = routed_to
        return response

    for entry in matching_entries:
        addr = entry.get("api_addr", "")
        if not addr:
            continue
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
            if body.tools:
                payload["tools"] = body.tools
            if body.tool_choice is not None:
                payload["tool_choice"] = body.tool_choice

            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    resp_msg = data.get("choices", [{}])[0].get("message", {})
                    text = resp_msg.get("content") or ""
                    resp_tool_calls = resp_msg.get("tool_calls")
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
                                message=ChatMessage(
                                    role="assistant",
                                    content=text or None,
                                    tool_calls=resp_tool_calls,
                                ),
                                finish_reason=data.get("choices", [{}])[0].get("finish_reason", "stop"),
                            )
                        ],
                        usage=Usage(
                            prompt_tokens=usage.get("prompt_tokens", 0),
                            completion_tokens=usage.get("completion_tokens", 0),
                            total_tokens=usage.get("total_tokens", 0),
                        ),
                    )
                    response = JSONResponse(content=resp_data.model_dump(exclude_none=True))
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


@router.get("/models/capabilities")
async def model_capabilities(request: Request):
    """List all available models with detailed capabilities.

    Returns model metadata (params, quantization, context length, features,
    throughput, queue depth, source) for intelligent routing decisions.
    """
    from mycellm.inference.reasoning_dialects import supports_thinking
    node = request.app.state.node
    models = []

    # Local models — full detail
    for m in node.inference.loaded_models:
        queue = node.inference.queue_status.get(m.name, 0)
        models.append({
            "id": m.name,
            "source": "local",
            "status": "loaded",
            "param_count_b": m.param_count_b,
            "quantization": m.quant,
            "context_length": m.ctx_len,
            "backend": m.backend,
            "features": m.features or ["streaming"],
            "throughput_tok_s": m.throughput_tok_s,
            "tier": m.tier or "",
            "tags": m.tags,
            "queue_depth": queue,
            "max_concurrent": node.inference._max_concurrent,
            "supports_grammar": m.backend == "llama.cpp",
            "supports_thinking": supports_thinking(m.name),
        })

    # QUIC peers
    for entry in node.registry.connected_peers():
        for m in entry.capabilities.models:
            models.append({
                "id": m.name,
                "source": "quic",
                "peer_id": entry.peer_id,
                "status": "remote",
                "param_count_b": m.param_count_b,
                "quantization": m.quant,
                "context_length": m.ctx_len,
                "backend": m.backend,
                "features": m.features or ["streaming"],
                "throughput_tok_s": m.throughput_tok_s,
                "tier": m.tier or "",
                "tags": m.tags,
                "supports_grammar": m.backend == "llama.cpp",
                "supports_thinking": supports_thinking(m.name),
            })

    # Fleet nodes
    for entry in node.node_registry.values():
        if entry.get("status") != "approved":
            continue
        caps = entry.get("capabilities", {})
        for m_data in caps.get("models", []):
            m = m_data if isinstance(m_data, dict) else {"name": m_data}
            name = m.get("name", "")
            models.append({
                "id": name,
                "source": "fleet",
                "peer_id": entry.get("peer_id", ""),
                "node_name": entry.get("node_name", ""),
                "status": "remote",
                "param_count_b": m.get("param_count_b", 0),
                "quantization": m.get("quant", ""),
                "context_length": m.get("ctx_len", 4096),
                "backend": m.get("backend", "unknown"),
                "features": m.get("features", ["streaming"]),
                "throughput_tok_s": m.get("throughput_tok_s", 0),
                "tier": m.get("tier", ""),
                "tags": m.get("tags", []),
                "supports_grammar": m.get("backend", "") == "llama.cpp",
                "supports_thinking": supports_thinking(name),
            })

    return {"models": models}


@router.get("/models/{model_id}")
async def retrieve_model(request: Request, model_id: str):
    """Retrieve a single model by ID (OpenAI-compatible)."""
    node = request.app.state.node

    # "auto" is a virtual model — always available when any model is reachable
    if model_id == "auto":
        return {
            "id": "auto",
            "object": "model",
            "created": int(time.time()),
            "owned_by": "mycellm",
        }

    # Check local models
    for m in node.inference.loaded_models:
        if m.name == model_id:
            return {
                "id": m.name,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "local",
            }

    # Check QUIC peers
    for entry in node.registry.connected_peers():
        for m in entry.capabilities.models:
            if m.name == model_id:
                return {
                    "id": m.name,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": f"peer:{entry.peer_id[:8]}",
                }

    # Check fleet
    for entry in node.node_registry.values():
        if entry.get("status") != "approved":
            continue
        caps = entry.get("capabilities", {})
        for m in caps.get("models", []):
            name = m.get("name", m) if isinstance(m, dict) else m
            if name == model_id:
                return {
                    "id": name,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": f"fleet:{entry.get('node_name', entry.get('peer_id', '')[:8])}",
                }

    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=404, content={"error": "not found"})


@router.get("/models")
async def list_models(request: Request):
    """List available models (local + remote via QUIC + fleet via registry).

    Model names are normalized — transport-internal prefixes like `relay:`
    are stripped so the public model list matches what consumers can
    actually request.
    """
    from mycellm.protocol.capabilities import normalize_model_name

    node = request.app.state.node
    models = []

    # Virtual "auto" model — mycellm auto-selects the best available
    models.append({
        "id": "auto",
        "object": "model",
        "created": int(time.time()),
        "owned_by": "mycellm",
    })

    # Local models
    for m in node.inference.loaded_models:
        models.append({
            "id": normalize_model_name(m.name),
            "object": "model",
            "created": int(time.time()),
            "owned_by": "local",
        })

    seen = {normalize_model_name(m.name) for m in node.inference.loaded_models}

    # Remote models from QUIC-connected peers
    for entry in node.registry.connected_peers():
        for m in entry.capabilities.models:
            display = normalize_model_name(m.name)
            if display not in seen:
                models.append({
                    "id": display,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": f"peer:{entry.peer_id[:8]}",
                })
                seen.add(display)

    # Fleet models from registry (HTTP-routable)
    for entry in node.node_registry.values():
        if entry.get("status") != "approved":
            continue
        caps = entry.get("capabilities", {})
        for m in caps.get("models", []):
            raw = m.get("name", m) if isinstance(m, dict) else m
            display = normalize_model_name(raw)
            if display not in seen:
                models.append({
                    "id": display,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": f"fleet:{entry.get('node_name', entry.get('peer_id', '')[:8])}",
                })
                seen.add(display)

    return {"object": "list", "data": models}


class EmbeddingsRequest(BaseModel):
    model: str = ""
    # A string or a list of strings. OpenAI also allows token arrays
    # (list[int] / list[list[int]]) — those are rejected with a 400 because
    # local backends tokenize text themselves.
    input: str | list = ""


@router.post("/embeddings")
async def create_embeddings(request: Request, body: EmbeddingsRequest):
    """OpenAI-compatible embeddings endpoint."""
    from fastapi.responses import JSONResponse
    from mycellm.inference.base import EmbeddingRequest, EmbeddingsNotSupportedError

    node = request.app.state.node

    def _error(status: int, message: str, err_type: str, code: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={"error": {"message": message, "type": err_type, "code": code}},
        )

    texts = body.input if isinstance(body.input, list) else [body.input]
    if not texts:
        return _error(
            400, "'input' must be a non-empty string or list of strings.",
            "invalid_request_error", "invalid_input",
        )
    if not all(isinstance(t, str) for t in texts):
        return _error(
            400,
            "Token array input is not supported — send 'input' as a string or list of strings.",
            "invalid_request_error", "invalid_input",
        )

    requested_model = body.model if body.model != "auto" else ""
    model_name = node.inference.resolve_model_name(requested_model)
    if not model_name:
        detail = (
            f"Model '{body.model}' not found. No loaded model serves embeddings."
            if requested_model else "No models loaded."
        )
        return _error(400, detail, "invalid_request_error", "model_not_found")

    try:
        result = await node.inference.embed(EmbeddingRequest(input=texts, model=model_name))
    except EmbeddingsNotSupportedError as e:
        return _error(400, str(e), "invalid_request_error", "embeddings_not_supported")
    except RuntimeError as e:
        if "busy" in str(e).lower() or "timed out" in str(e).lower():
            return _error(503, str(e), "model_busy", "model_busy")
        return _error(500, str(e), "server_error", "inference_error")
    except Exception as e:
        logger.error(f"Embeddings failed for {model_name}: {e}")
        return _error(500, str(e), "server_error", "inference_error")

    data = [
        {"object": "embedding", "index": i, "embedding": emb}
        for i, emb in enumerate(result.embeddings)
    ]
    return {
        "object": "list",
        "data": data,
        "model": model_name,
        "usage": {
            "prompt_tokens": result.total_tokens,
            "total_tokens": result.total_tokens,
        },
    }
