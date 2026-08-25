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

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mycellm.api.client_ip import client_address
from mycellm.protocol.capabilities import classify_tier


def _client_ip(request) -> str:
    """Caller address, honouring a trusted proxy's forwarded header.

    Without this the anon rate limit on a containerised bootstrap applies to
    every visitor at once, because they all arrive as the bridge gateway.
    """
    settings = getattr(request.app.state, "settings", None)
    from mycellm.api.client_ip import DEFAULT_TRUSTED_PROXIES
    spec = getattr(settings, "trusted_proxies", None) or DEFAULT_TRUSTED_PROXIES
    return client_address(request, spec)

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
    """Select the best available Tier 1 model (first candidate).

    Returns (model_name, fleet_addr) — fleet_addr is None for local models.
    """
    candidates = _get_candidates(node)
    if candidates:
        return candidates[0][0], candidates[0][1]
    return None, None


_round_robin_counter = 0


def _is_vision_model(name: str, backend: str = "") -> bool:
    """True if a candidate can accept image input — by advertised backend
    (``mlx-vlm``) or a vision marker in the model name (fallback for models
    served via other backends or where backend isn't advertised)."""
    if (backend or "").lower() == "mlx-vlm":
        return True
    n = (name or "").lower()
    return any(tok in n for tok in ("-vl-", "-vl", "vl-", "vision", "llava", "pixtral", "smolvlm", "-vlm"))


#: String tier ranking, matching `router.model_resolver`. The gateway sorts by
#: `classify_tier`'s integer tiers (a capacity decision: smallest first), but a
#: caller's floor is expressed in the same four names the rest of the API uses,
#: so the two must not be conflated.
_TIER_RANK = {"frontier": 4, "capable": 3, "fast": 2, "tiny": 1}


def _get_candidates(
    node,
    require_vision: bool = False,
    min_tier: str = "",
    want_model: str = "",
) -> list[tuple[str, str | None, int]]:
    """Get all available model candidates, load-balanced across equal tiers.

    Returns list of (model_name, fleet_addr_or_None, tier).
    Candidates within the same tier are rotated via round-robin so
    concurrent requests spread across models/nodes instead of always
    hitting the same one first. When ``require_vision`` is set (the request
    carries an image), only vision-capable candidates are kept — otherwise an
    image could be load-balanced onto a text model that silently drops it.

    ``min_tier`` and ``want_model`` narrow the field to what the caller asked
    for. Both are **filters, not preferences**: an empty result is returned as
    an empty result, so the caller can say "nothing meets that floor" instead
    of quietly serving a 1B model to someone who asked for frontier. That
    silent downgrade is the failure this signature exists to prevent.
    """
    global _round_robin_counter
    _round_robin_counter += 1

    candidates = []  # (name, fleet_addr, tier, backend, param_b)

    # Local models (preferred — no network hop)
    for m in node.inference.loaded_models:
        candidates.append((m.name, None, classify_tier(m.param_count_b),
                           getattr(m, "backend", ""), m.param_count_b or 0.0))

    # QUIC-connected peers (can serve inference over existing connection — NAT-friendly)
    for entry in node.registry.connected_peers():
        for m in entry.capabilities.models:
            candidates.append((m.name, f"quic:{entry.peer_id}", classify_tier(m.param_count_b),
                               getattr(m, "backend", ""), m.param_count_b or 0.0))

    # Fleet nodes (HTTP proxy — requires reachable api_addr)
    import time as _time
    for entry in node.node_registry.values():
        if entry.get("status") != "approved":
            continue
        if _time.time() - entry.get("last_seen", 0) > 120:
            continue  # offline
        addr = entry.get("api_addr", "")
        for m in entry.get("capabilities", {}).get("models", []):
            if isinstance(m, dict):
                _pb = float(m.get("param_count_b", 0) or 0)
                candidates.append((m.get("name", ""), addr, classify_tier(_pb),
                                   m.get("backend", ""), _pb))
            else:
                candidates.append((m, addr, 1, "", 0.0))

    # Multimodal requests must go to a vision-capable model.
    if require_vision:
        candidates = [c for c in candidates if _is_vision_model(c[0], c[3])]

    # A named model. Compared on the normalised name because that is what the
    # model list advertises, and a caller can only ask for what it was shown.
    if want_model:
        from mycellm.protocol.capabilities import normalize_model_name
        target = normalize_model_name(want_model)
        candidates = [c for c in candidates if normalize_model_name(c[0]) == target]

    # A quality floor. `derive_tier` from the advertised parameter count, or
    # from the name when nothing was advertised — the same order of preference
    # the resolver uses, so the gateway and the node agree on what "capable"
    # means.
    floor = _TIER_RANK.get(min_tier, 0)
    if floor:
        from mycellm.router.model_resolver import derive_tier, parse_param_count

        def _rank(cand) -> int:
            # An advertised count wins; otherwise read the name. A model whose
            # size is unknown ranks 0 and is excluded by ANY floor — assuming
            # 7B here would let "fast" quietly admit unsized models.
            param_b = cand[4] or parse_param_count(cand[0])
            return _TIER_RANK.get(derive_tier(param_b), 0) if param_b else 0

        candidates = [c for c in candidates if _rank(c) >= floor]

    # Group by tier, rotate within each tier via round-robin
    from itertools import groupby
    # Order within a tier: local, then QUIC, then HTTP proxy.
    #
    # ⚠️ THE OLD KEY WAS `0 if c[1] is None else 1`, WHICH ONLY PROMOTED LOCAL
    # MODELS. A `quic:<peer>` candidate has a non-None fleet_addr, so it sorted
    # level with the HTTP-proxy candidates and round-robin could put the proxy
    # first — even though the comments above call QUIC the NAT-friendly path and
    # the HTTP path explicitly "requires reachable api_addr". On a bootstrap
    # whose peers are all behind NAT that address can never be reachable, so the
    # proxy attempt only ever buys a 30s connect timeout before failover.
    def _hop_cost(addr) -> int:
        if addr is None:
            return 0            # local — no network hop
        if str(addr).startswith("quic:"):
            return 1            # existing connection, NAT-friendly
        return 2                # HTTP proxy — needs a routable api_addr

    candidates.sort(key=lambda c: (c[2], _hop_cost(c[1])))
    rotated = []
    # Rotate within (tier, hop cost), NOT within tier alone. Grouping by tier
    # only would let the round-robin offset lift an HTTP-proxy candidate back
    # above the QUIC one the sort just placed ahead of it — undoing the
    # ordering on roughly half of all requests. Load is still spread across
    # peers; it is spread among equally-good ones.
    for _key, group in groupby(candidates, key=lambda c: (c[2], _hop_cost(c[1]))):
        items = list(group)
        if len(items) > 1:
            offset = _round_robin_counter % len(items)
            items = items[offset:] + items[:offset]
        rotated.extend(items)

    return [(c[0], c[1], c[2]) for c in rotated]


@router.get("/models")
async def public_models(request: Request):
    """What the public gateway can actually serve, right now.

    ⚠️ THIS IS DERIVED FROM `_get_candidates`, NOT FROM THE NODE'S MODEL LIST,
    and the difference is the entire point. `/v1/models` answers "what does
    this node know about"; a visitor to the public chat box needs "what will
    answer me if I press send". Those diverge constantly — an unapproved fleet
    node, a peer that dropped its connection, a vision-only backend — and a
    selector populated from the wrong one offers choices that can only fail.

    Deduplicated by model name: the same model on three nodes is one choice to
    a visitor, who cannot address a specific node here anyway.
    """
    from mycellm.router.model_resolver import derive_tier, parse_param_count

    node = request.app.state.node
    seen: dict[str, dict] = {}
    for name, _addr, _tier in _get_candidates(node):
        if not name or name in seen:
            continue
        # Empty when the name carries no size. Guessing would put a number on
        # the public chat box's tier counts that nobody measured.
        known = parse_param_count(name)
        seen[name] = {
            "id": name,
            "tier": derive_tier(known) if known else "",
            "vision": _is_vision_model(name),
        }

    tiers = {t: 0 for t in _TIER_RANK}
    for entry in seen.values():
        floor = _TIER_RANK.get(entry["tier"], 0)
        # A model counts toward every floor it satisfies, matching how a floor
        # is enforced — otherwise the counts shown next to "Capable" would
        # exclude the frontier models that also qualify.
        for tier_name, rank in _TIER_RANK.items():
            if floor >= rank:
                tiers[tier_name] += 1

    return {
        "object": "list",
        "data": sorted(seen.values(), key=lambda m: (-_TIER_RANK.get(m["tier"], 0), m["id"])),
        "tiers": tiers,
    }


@router.post("/receipts")
async def submit_receipts(request: Request):
    """Ingest co-signed receipts into the tracker's per-network ledger.

    Public + unauthenticated by design — the security is the consumer
    co-signature + pubkey↔peer_id binding (verified per receipt), not an API
    key. Body: {"receipts": [ {consumer, seeder, model, tokens, cost,
    request_id, ts, network_id?, seeder_signature, consumer_signature,
    seeder_pubkey, consumer_pubkey}, ... ]}.
    """
    node = request.app.state.node
    if not getattr(node, "ledger", None):
        return JSONResponse(status_code=503, content={"error": {"message": "Tracker ledger unavailable."}})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "Invalid JSON"}})

    receipts = body.get("receipts", [])
    if not isinstance(receipts, list):
        return JSONResponse(status_code=400, content={"error": {"message": "'receipts' must be a list"}})

    from mycellm.accounting.tracker import settle_cosigned_receipt
    from mycellm.storage.repositories import NetworkLedgerRepository
    tracker_ledger = NetworkLedgerRepository()
    results, settled = [], 0
    for r in receipts[:100]:  # cap submissions per request
        if not isinstance(r, dict):
            results.append({"ok": False, "reason": "malformed"})
            continue
        validator = getattr(node, "tracker_validator", None) or node.receipt_validator
        res = await settle_cosigned_receipt(tracker_ledger, validator, r)
        if res.get("ok"):
            settled += 1
        results.append({"request_id": r.get("request_id", ""), **res})
    return {"received": len(receipts), "settled": settled, "results": results}


@router.get("/credits/{peer_id}")
async def get_peer_credits(peer_id: str, request: Request):
    """The tracker's authoritative credit balance for a peer on a network.

    Query: ?network_id=... (default ''). Public — balances aren't secret and a
    node reads its own to reconcile its local cache against the source of truth.
    """
    node = request.app.state.node
    if not getattr(node, "ledger", None):
        return JSONResponse(status_code=503, content={"error": {"message": "Tracker ledger unavailable."}})
    network_id = request.query_params.get("network_id", "")
    from mycellm.storage.repositories import NetworkLedgerRepository
    repo = NetworkLedgerRepository()
    acct = await repo.get_account(peer_id, network_id)
    served = await repo.served_count(peer_id)
    if not acct:
        return {"peer_id": peer_id, "network_id": network_id, "tracked": False,
                "balance": 0.0, "total_earned": 0.0, "total_spent": 0.0, "served": served}
    return {"peer_id": peer_id, "network_id": network_id, "tracked": True,
            "balance": acct["balance"], "total_earned": acct["total_earned"],
            "total_spent": acct["total_spent"], "served": served}


@router.post("/chat/completions")
async def public_chat(request: Request):
    """Public chat completions — rate-limited, Tier 1 only, no auth required.

    OpenAI-compatible request/response format. Streaming supported via SSE.
    """
    from mycellm.activity import EventType
    from mycellm.inference.base import InferenceRequest

    node = request.app.state.node
    client_ip = _client_ip(request)

    # Parse request body
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": {"message": "Invalid JSON"}})

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse(status_code=400, content={"error": {"message": "messages required"}})

    # Extract textual content per message. content may be a string or a list
    # of multimodal parts; length-check and the privacy scan operate on text.
    from mycellm.inference.base import content_to_text
    msg_texts = [content_to_text(msg.get("content", "")) for msg in messages]

    # Validate message lengths
    for text in msg_texts:
        if len(text) > _MAX_MESSAGE_LENGTH:
            return JSONResponse(status_code=400, content={
                "error": {"message": f"Message too long (max {_MAX_MESSAGE_LENGTH} chars)"}
            })

    # Sensitive data guard (server-side enforcement)
    # Bypass with X-Privacy-Override: acknowledged header (explicit opt-out)
    privacy_override = request.headers.get("x-privacy-override", "") == "acknowledged"
    if not privacy_override:
        from mycellm.privacy import scan_with_policy
        all_content = " ".join(msg_texts)
        guard_result = scan_with_policy(all_content, trust_level="untrusted")
    else:
        guard_result = {"action": "allow", "matches": [], "highest_severity": "none"}
    if guard_result["action"] == "block":
        high_matches = [m for m in guard_result["matches"] if m.severity == "high"]
        labels = [f"{m.label}: {m.pattern}" for m in high_matches[:3]]
        return JSONResponse(status_code=422, content={
            "error": {
                "message": "Sensitive data detected in prompt. For privacy, this request was blocked.",
                "type": "sensitive_data_guard",
                "details": labels,
                "hint": "Use a local model or private network for sensitive prompts. "
                        "Set X-Privacy-Override: acknowledged to bypass (not recommended).",
            }
        })

    # Rate limit check
    allowed, reason = _check_rate(client_ip)
    if not allowed:
        return JSONResponse(status_code=429, content={"error": {"message": reason}})

    # Multimodal: if the prompt carries an image, only vision-capable models can
    # serve it (a text model would silently flatten the image to text). Filter
    # candidates so an image never lands on a text model.
    has_image = any(
        isinstance(m.get("content"), list) and
        any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
        for m in messages
    )

    # What the caller asked for. Both are optional and both are honoured as
    # written: "auto" and "" mean the gateway chooses, anything else narrows.
    want_model = str(body.get("model", "") or "").strip()
    if want_model.lower() in ("auto", "default"):
        want_model = ""
    _opts = body.get("mycellm") if isinstance(body.get("mycellm"), dict) else {}
    min_tier = str((_opts or {}).get("min_tier", "") or "").strip().lower()
    if min_tier and min_tier not in _TIER_RANK:
        return JSONResponse(status_code=400, content={"error": {
            "message": f"Unknown tier '{min_tier}'.",
            "type": "invalid_tier",
            "hint": f"Valid tiers: {', '.join(_TIER_RANK)}.",
        }})

    # Get all available candidates, try each until one succeeds
    candidates = _get_candidates(
        node, require_vision=has_image, min_tier=min_tier, want_model=want_model
    )
    if not candidates:
        # ⚠️ SAY WHICH CONSTRAINT EMPTIED THE FIELD. A bare "no models
        # available" after an explicit choice reads as "the network is down"
        # when the truth is "the network is up and does not have that" — and
        # the two have completely different fixes. Reporting the narrowest
        # true cause is also what stops a tier floor from decaying into a
        # suggestion: the request is refused, not quietly downgraded.
        if want_model:
            msg = f"No node is currently serving '{want_model}'."
        elif min_tier:
            reachable = len(_get_candidates(node, require_vision=has_image))
            msg = (f"No {min_tier}-tier model is available right now "
                   f"({reachable} model(s) reachable below that tier).")
        elif has_image:
            msg = "No vision-capable model is available for image input right now. Try again later."
        else:
            msg = "No models currently available. Try again later."
        return JSONResponse(status_code=503, content={"error": {
            "message": msg,
            "type": "no_candidate",
            "requested_model": want_model or None,
            "requested_tier": min_tier or None,
        }})

    stream = body.get("stream", False)
    # Accept OpenAI's newer max_completion_tokens as an alias for max_tokens.
    _req_max = body.get("max_tokens")
    if _req_max is None:
        _req_max = body.get("max_completion_tokens", _MAX_REQUEST_TOKENS)
    max_tokens = min(_req_max, _MAX_REQUEST_TOKENS)
    temperature = body.get("temperature", 0.7)
    # Reasoning suppression: explicit body.reasoning.exclude wins, else fall
    # back to MYCELLM_HIDE_REASONING_BY_DEFAULT. Public bootstraps should set
    # the env var so demo visitors see clean answers by default.
    from mycellm.api.openai import _resolve_reasoning_exclude
    reasoning_exclude = _resolve_reasoning_exclude(body.get("reasoning"))

    start_time = time.time()
    request_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"

    # Try candidates in order — failover to next if busy/error
    last_error = ""
    for model_name, fleet_addr, _tier in candidates:
        try:
            # QUIC peer — route over existing connection (NAT-friendly)
            if fleet_addr and fleet_addr.startswith("quic:"):
                peer_id = fleet_addr[5:]
                try:
                    if stream:
                        # True streaming: yield tokens as they arrive from peer
                        from fastapi.responses import StreamingResponse
                        _model = model_name
                        _peer = peer_id

                        async def _quic_stream_real():
                            from mycellm.inference.reasoning_dialects import make_splitter
                            splitter = make_splitter(_model)
                            token_count = 0

                            def _envelope(delta: dict, finish: str | None = None) -> str:
                                return f"data: {json.dumps({'id': request_id, 'object': 'chat.completion.chunk', 'model': _model, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}], 'mycellm': {'node': _node_hash(_peer), 'served_by': 'mycellm-public'}})}\n\n"

                            async for chunk in node.route_inference_stream(
                                _model, messages,
                                temperature=temperature,
                                max_tokens=max_tokens,
                            ):
                                text = chunk.get("text", "")
                                token_count += 1
                                if text:
                                    for kind, piece in splitter.feed(text):
                                        if kind == "content":
                                            yield _envelope({"content": piece}, chunk.get("finish_reason"))
                                        elif kind == "reasoning" and not reasoning_exclude:
                                            yield _envelope({"reasoning_content": piece}, chunk.get("finish_reason"))
                            # Drain — handles unclosed <think> at stream end
                            for kind, piece in splitter.flush():
                                if kind == "content":
                                    yield _envelope({"content": piece})
                                elif kind == "reasoning" and not reasoning_exclude:
                                    yield _envelope({"reasoning_content": piece})
                            latency_ms = round((time.time() - start_time) * 1000)
                            _record_usage(client_ip, token_count)
                            node.activity.record(
                                EventType.INFERENCE_COMPLETE,
                                model=_model, source="public_gateway_quic_stream",
                                tokens=token_count, latency_ms=latency_ms,
                            )
                            yield "data: [DONE]\n\n"

                        return StreamingResponse(_quic_stream_real(), media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

                    # Non-streaming: wait for full response. Forward
                    # reasoning_exclude so the homelab peer can pass
                    # enable_thinking=False to the model's template
                    # (closes the empty-content-on-low-max_tokens bug
                    # where thinking ate the budget then got stripped).
                    result = await node.route_inference(
                        model_name, messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        reasoning_exclude=reasoning_exclude,
                    )
                    if result:
                        text = result.get("text", "") if isinstance(result, dict) else ""
                        prompt_tokens = result.get("prompt_tokens", 0) if isinstance(result, dict) else 0
                        completion_tokens = result.get("completion_tokens", 0) if isinstance(result, dict) else 0
                        latency_ms = round((time.time() - start_time) * 1000)
                        _record_usage(client_ip, prompt_tokens + completion_tokens)
                        node.activity.record(
                            EventType.INFERENCE_COMPLETE,
                            model=model_name, source="public_gateway_quic",
                            tokens=completion_tokens, latency_ms=latency_ms,
                        )
                        return _clean_response(request_id, model_name, text, "stop",
                            prompt_tokens, completion_tokens, latency_ms, node_id=_node_hash(peer_id),
                            reasoning_exclude=reasoning_exclude)
                except Exception as e:
                    last_error = str(e)
                    logger.info(f"Gateway QUIC failover: {model_name}@{peer_id[:8]} failed: {e}")
                    continue
                # route_inference returned None/falsy — skip to next candidate
                last_error = f"QUIC route to {peer_id[:8]} returned empty"
                logger.info(f"Gateway QUIC: {model_name}@{peer_id[:8]} returned no result")
                continue

            # HTTP fleet proxy
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
            inf_req = InferenceRequest(
                model=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_exclude=reasoning_exclude,
            )

            result = await node.inference.generate(inf_req)
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
                                   result.completion_tokens, latency_ms,
                                   reasoning_exclude=reasoning_exclude)

        except (RuntimeError, _FleetBusyError, _FleetUnavailableError) as e:
            # Busy (local queue timeout or fleet 503) or unreachable — next one.
            last_error = str(e)
            why = "unreachable" if isinstance(e, _FleetUnavailableError) else "busy"
            logger.info(f"Gateway failover: {model_name}{'@'+fleet_addr if fleet_addr else ''} {why}, trying next")
            continue
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Gateway candidate {model_name} failed: {e}")
            continue

    # All candidates exhausted
    logger.warning(f"Public gateway: all {len(candidates)} candidates failed. Last: {last_error}")
    node.activity.record(EventType.INFERENCE_FAILED, model=candidates[0][0], source="public_gateway")
    return JSONResponse(status_code=503, content={
        "error": {"message": "All models are busy right now. Please try again in a moment."}
    })


def _node_hash(addr: str) -> str:
    """Generate an anonymized 8-char hash of a node address for attribution."""
    import hashlib
    return hashlib.sha256(addr.encode()).hexdigest()[:8]


def _clean_response(request_id, model_name, text, finish_reason, prompt_tokens, completion_tokens, latency_ms, node_id="", reasoning_exclude=True):
    """Build a clean, metadata-stripped OpenAI-compatible response.

    Applies reasoning split: <think>...</think> blocks are stripped from
    content (and routed to reasoning_content when caller opts in).
    """
    from mycellm.inference.reasoning_dialects import split_reasoning
    content, reasoning = split_reasoning(text or "", model_name)
    message: dict = {"role": "assistant", "content": content or ""}
    if reasoning and not reasoning_exclude:
        message["reasoning_content"] = reasoning
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{
            "index": 0,
            "message": message,
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


class _FleetBusyError(Exception):
    """Raised when a fleet node returns 503 (model busy) — triggers failover."""
    pass


class _FleetUnavailableError(Exception):
    """Raised when a fleet node cannot be reached at all — triggers failover.

    Distinct from `_FleetBusyError` only for log legibility: busy means the node
    answered and declined, unreachable means the address is wrong or dead. Both
    mean "try the next candidate", which is the part that matters.
    """
    pass


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
                    # 503 = model busy — propagate to failover loop
                    if resp.status_code == 503:
                        raise _FleetBusyError(f"Fleet {fleet_addr} model {model_name} busy")
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

        # NOTE: _proxy_fleet doesn't have access to the request-level
        # reasoning_exclude in this helper signature; default to True (hidden)
        # since this path serves the public gateway whose policy is hide-by-default.
        return _clean_response(
            request_id, model_name,
            choice.get("message", {}).get("content", ""),
            choice.get("finish_reason", "stop"),
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            latency_ms,
            node_id=_node_hash(fleet_addr),
            reasoning_exclude=True,
        )

    except _FleetBusyError:
        # Busy is already a failover signal — let it through untouched.
        raise
    except Exception as e:
        # ⚠️ THIS USED TO `return JSONResponse(503)` AND THAT ENDED THE REQUEST.
        # The gateway builds a candidate list and loops over it, failing over on
        # any exception — but returning a response from here instead of raising
        # exits the loop with a 503 while working candidates were still queued
        # behind this one. `_stream_fleet` has always raised; only this
        # non-streaming twin swallowed the failure, so streaming requests failed
        # over correctly and non-streaming ones did not.
        #
        # It took a production outage to surface: every node announcing to a
        # Dockerised bootstrap is recorded with the bridge-gateway address
        # (`/v1/admin/nodes/announce` substitutes the request's client IP for an
        # `0.0.0.0` api_addr, and inside Docker that is the bridge), so the HTTP
        # candidate is unreachable by construction — and it killed the request
        # instead of deferring to the QUIC candidate for the very same model.
        logger.warning(f"Fleet proxy to {fleet_addr} failed: {e}")
        node.activity.record(EventType.INFERENCE_FAILED, model=model_name, source="public_gateway_fleet")
        raise _FleetUnavailableError(f"fleet {fleet_addr} unreachable: {e}") from e


async def _stream_fleet(node, request_id, model_name, fleet_addr, messages, temperature, max_tokens, client_ip, start_time):
    """Stream a response from a fleet node via SSE proxy.

    Raises _FleetBusyError on 503 so the gateway failover loop can try next candidate.
    """
    import httpx
    from fastapi.responses import StreamingResponse
    from mycellm.activity import EventType

    base = fleet_addr if fleet_addr.startswith("http") else f"http://{fleet_addr}"

    # Pre-flight: open the connection and check status before committing to StreamingResponse
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=180.0))
    resp_ctx = client.stream("POST", f"{base}/v1/chat/completions", json={
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    })
    upstream_resp = await resp_ctx.__aenter__()
    if upstream_resp.status_code == 503:
        await resp_ctx.__aexit__(None, None, None)
        await client.aclose()
        raise _FleetBusyError(f"Fleet {fleet_addr} model {model_name} busy")
    if upstream_resp.status_code != 200:
        await resp_ctx.__aexit__(None, None, None)
        await client.aclose()
        raise Exception(f"Fleet {fleet_addr} returned {upstream_resp.status_code}")

    node_id = _node_hash(fleet_addr)

    async def generate():
        total_tokens = 0
        first_chunk = True
        try:
            async for line in upstream_resp.aiter_lines():
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
                    if first_chunk:
                        out["mycellm"] = {"node": node_id, "served_by": "mycellm-public"}
                        first_chunk = False
                    yield f"data: {json.dumps(out)}\n\n"
                    if finish:
                        break
                except json.JSONDecodeError:
                    continue

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
            try:
                await resp_ctx.__aexit__(None, None, None)
                await client.aclose()
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

            async for chunk in node.inference.generate_stream(inf_req):
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
