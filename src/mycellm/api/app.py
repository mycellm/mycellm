"""FastAPI application factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from mycellm.api.admin import router as admin_router
from mycellm.api.gateway import router as gateway_router
from mycellm.api.models import router as models_router
from mycellm.api.node import router as node_router
from mycellm.api.openai import router as openai_router

if TYPE_CHECKING:
    from mycellm.node import MycellmNode


# Paths that never require auth
_PUBLIC_PATHS = {"/health", "/metrics", "/docs", "/openapi.json"}
_PUBLIC_PREFIXES = ("/health",)  # dashboard static files served at /

# Inference endpoints that are left unauthenticated when running in
# `public bootstrap` mode (MYCELLM_PUBLIC=true). Anyone can hit these; anon
# rate limiting (per-IP token bucket) keeps them from being abused.
_PUBLIC_MODE_INFERENCE_PATHS = {
    "/v1/models",
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/embeddings",
}
_PUBLIC_MODE_INFERENCE_PREFIXES = (
    "/v1/models/",   # /v1/models/{id}
    "/api/",         # Ollama-compatible routes (/api/tags, /api/chat, /api/show)
)


def _is_public_inference_path(path: str) -> bool:
    if path in _PUBLIC_MODE_INFERENCE_PATHS:
        return True
    return any(path.startswith(p) for p in _PUBLIC_MODE_INFERENCE_PREFIXES)


class AnonRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP token bucket for anonymous inference on public bootstraps.

    Inference paths are unauthenticated in public mode (so anyone can use
    them), but unlimited anon access is a footgun. This middleware applies
    a simple per-IP request budget for anon callers and 429s above the rate.

    Authenticated callers (valid api_key on the request) bypass entirely.
    """

    def __init__(self, app, api_key: str, max_per_min: int = 30):
        super().__init__(app)
        self.api_key = api_key
        self.max_per_min = max_per_min
        self._buckets: dict[str, list[float]] = {}

    def _is_authenticated(self, request: Request) -> bool:
        if not self.api_key:
            return False
        if request.headers.get("authorization", "") == f"Bearer {self.api_key}":
            return True
        if request.headers.get("x-api-key") == self.api_key:
            return True
        if request.query_params.get("api_key") == self.api_key:
            return True
        return False

    async def dispatch(self, request: Request, call_next):
        import time
        path = request.url.path
        if not _is_public_inference_path(path):
            return await call_next(request)
        if self._is_authenticated(request):
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"
        now = time.time()
        bucket = [t for t in self._buckets.get(ip, []) if now - t < 60]
        if len(bucket) >= self.max_per_min:
            retry_after = max(1, int(60 - (now - bucket[0])))
            try:
                from mycellm.metrics import bootstrap_anon_rate_limited_total
                bootstrap_anon_rate_limited_total.inc()
            except Exception:
                pass
            return JSONResponse(
                status_code=429,
                content={
                    "error": "rate_limited",
                    "message": (
                        f"Anonymous inference limit ({self.max_per_min}/min) reached. "
                        f"Retry in {retry_after}s, or send an api_key for higher limits."
                    ),
                },
                headers={"Retry-After": str(retry_after)},
            )
        bucket.append(now)
        self._buckets[ip] = bucket
        if len(self._buckets) > 5000:
            # prune empties
            self._buckets = {
                k: [t for t in v if now - t < 60]
                for k, v in self._buckets.items()
            }
            self._buckets = {k: v for k, v in self._buckets.items() if v}
        return await call_next(request)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Require Bearer token on API routes when MYCELLM_API_KEY is set.

    Two modes:

    **Private (default, `public=False`):**
      Escalating brute-force lockout:
        - 5 failed attempts → 60s lockout
        - 10 failed attempts → 15min lockout
        - 20+ failed attempts → 1hr lockout

    **Public bootstrap (`public=True`):**
      Inference paths are unauthenticated. Admin/node paths still need the
      key but use a lenient sliding window (no escalation, no cross-path
      lockout) so a scanner hammering /v1/node/* can't lock out a legit
      admin on /v1/chat/completions on a shared-NAT IP. Self-DoS avoidance
      is the priority — this is a public gateway.
    """

    _MAX_ATTEMPTS = [5, 10, 20]  # thresholds
    _LOCKOUT_SECS = [60, 900, 3600]  # 1min, 15min, 1hr

    # Public-mode sliding window: N failed admin auths per W seconds → 429
    _PUBLIC_WINDOW_SECS = 300
    _PUBLIC_WINDOW_MAX_FAILS = 30

    def __init__(self, app, api_key: str, public: bool = False):
        super().__init__(app)
        self.api_key = api_key
        self.public = public
        # {ip: {"failures": N, "locked_until": timestamp}}
        self._auth_state: dict[str, dict] = {}
        # Public-mode: {ip: [timestamps of failed auths]} sliding window
        self._public_fails: dict[str, list[float]] = {}

    def _get_lockout(self, failures: int) -> int:
        for threshold, lockout in zip(reversed(self._MAX_ATTEMPTS), reversed(self._LOCKOUT_SECS)):
            if failures >= threshold:
                return lockout
        return 0

    async def dispatch(self, request: Request, call_next):
        import time
        path = request.url.path

        # Skip auth for public paths, public stats API, node announce, and static assets
        if path in _PUBLIC_PATHS or not (path.startswith("/v1") or path.startswith("/api")):
            return await call_next(request)
        if "/public/" in path or path == "/v1/admin/nodes/announce":
            return await call_next(request)

        # Public bootstrap mode: inference endpoints bypass auth entirely
        if self.public and _is_public_inference_path(path):
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"

        # Private-mode escalating lockout check
        if not self.public:
            state = self._auth_state.get(ip, {"failures": 0, "locked_until": 0})
            if state["locked_until"] > time.time():
                remaining = int(state["locked_until"] - time.time())
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "locked",
                        "message": f"Too many failed attempts. Try again in {remaining}s.",
                    },
                )

        def _valid_key() -> bool:
            auth = request.headers.get("authorization", "")
            if auth == f"Bearer {self.api_key}":
                return True
            if request.headers.get("x-api-key") == self.api_key:
                return True
            if request.query_params.get("api_key") == self.api_key:
                return True
            return False

        if _valid_key():
            self._auth_state.pop(ip, None)
            self._public_fails.pop(ip, None)
            return await call_next(request)

        # Failed auth path
        if self.public:
            # Sliding window, no escalation. Only 429 if the same IP has
            # been genuinely hammering admin endpoints.
            now = time.time()
            fails = [t for t in self._public_fails.get(ip, []) if now - t < self._PUBLIC_WINDOW_SECS]
            fails.append(now)
            self._public_fails[ip] = fails
            if len(self._public_fails) > 2000:
                # prune
                self._public_fails = {
                    k: [t for t in v if now - t < self._PUBLIC_WINDOW_SECS]
                    for k, v in self._public_fails.items()
                }
                self._public_fails = {k: v for k, v in self._public_fails.items() if v}
            if len(fails) > self._PUBLIC_WINDOW_MAX_FAILS:
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limited",
                        "message": "Too many failed admin auth attempts. Slow down.",
                    },
                )
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "message": "Valid API key required"},
            )

        # Private-mode escalating lockout
        state["failures"] = state.get("failures", 0) + 1
        lockout = self._get_lockout(state["failures"])
        if lockout:
            state["locked_until"] = time.time() + lockout
        self._auth_state[ip] = state

        # Prune old entries (prevent memory leak)
        if len(self._auth_state) > 1000:
            cutoff = time.time() - 7200
            self._auth_state = {
                k: v for k, v in self._auth_state.items()
                if v.get("locked_until", 0) > cutoff or v.get("failures", 0) > 0
            }

        if lockout:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "locked",
                    "message": f"Too many failed attempts. Locked for {lockout}s.",
                },
            )

        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "message": "Valid API key required"},
        )


def create_app(node: MycellmNode) -> FastAPI:
    """Create the FastAPI application with all routes."""
    app = FastAPI(
        title="mycellm",
        description="Distributed LLM inference node API",
        version="0.1.0",
    )

    # GZip compression for responses > 500 bytes.
    # Compresses JSON API responses, dashboard HTML/JS/CSS, fleet proxy payloads.
    # SSE streams are excluded by default (Transfer-Encoding: chunked).
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # CORS: allow all origins — the embedded dashboard is served from the same
    # origin, so CORS doesn't add protection there. The real access control is
    # the API key middleware (MYCELLM_API_KEY). If you need to restrict origins,
    # set MYCELLM_CORS_ORIGINS as a comma-separated list.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API key auth (only active when MYCELLM_API_KEY is set)
    from mycellm.config import get_settings
    settings = get_settings()
    if settings.api_key:
        app.add_middleware(
            ApiKeyMiddleware,
            api_key=settings.api_key,
            public=settings.public,
        )

    # Per-IP anon rate limiter — only active in public bootstrap mode.
    # Inference endpoints are unauthenticated for the public, so this
    # prevents a single IP from monopolising the gateway. Authenticated
    # callers (valid api_key) bypass.
    if settings.public:
        app.add_middleware(
            AnonRateLimitMiddleware,
            api_key=settings.api_key,
            max_per_min=settings.public_anon_rate_per_min,
        )

    # Store node reference for route handlers
    app.state.node = node

    # API routes
    app.include_router(openai_router, prefix="/v1")
    app.include_router(node_router, prefix="/v1/node")
    app.include_router(admin_router, prefix="/v1/admin")
    app.include_router(models_router, prefix="/v1/node/models")
    app.include_router(gateway_router, prefix="/v1/public")

    # Ollama-compatible API routes
    # Clients like OpenClaw use the Ollama SDK which calls /api/tags,
    # /api/show, and /api/chat instead of the OpenAI-compatible endpoints.
    from mycellm.api.ollama_compat import ollama_router
    app.include_router(ollama_router, prefix="/api")

    # Health check (always public — includes auth posture for clients)
    @app.get("/health")
    async def health():
        from mycellm import __version__
        if settings.public:
            # Public bootstrap mode: inference is open, admin/node are keyed.
            protected = ["/v1/node/*", "/v1/admin/*"]
            public_paths = [
                "/health", "/metrics",
                "/v1/public/*", "/v1/admin/nodes/announce",
                "/v1/models", "/v1/models/*",
                "/v1/chat/*", "/v1/completions", "/v1/embeddings",
                "/api/*",
            ]
        elif settings.api_key:
            protected = ["/v1/node/*", "/v1/admin/*", "/v1/chat/*", "/v1/models", "/api/*"]
            public_paths = ["/health", "/metrics", "/v1/public/*", "/v1/admin/nodes/announce"]
        else:
            protected = []
            public_paths = ["*"]
        return {
            "status": "ok",
            "version": __version__,
            "peer_id": node.peer_id,
            "public_bootstrap": settings.public,
            "auth_required": bool(settings.api_key) and not settings.public,
            "auth": {
                "enabled": bool(settings.api_key),
                "public_mode": settings.public,
                "protected": protected,
                "public": public_paths,
            },
            "seeders_online": sum(
                1 for e in node.node_registry.values()
                if (__import__("time").time() - e.get("last_seen", 0)) < 120
                and e.get("status") == "approved"
            ) if hasattr(node, "node_registry") else 0,
        }

    # Prometheus metrics endpoint (always public)
    @app.get("/metrics")
    async def metrics():
        from fastapi.responses import Response
        from mycellm.metrics import collect_from_node, render_metrics
        collect_from_node(node)
        return Response(
            content=render_metrics(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    # Serve web dashboard with SPA fallback
    try:
        from importlib.resources import files
        from fastapi.responses import FileResponse, HTMLResponse
        import os

        web_dir = files("mycellm.web")
        # MultiplexedPath.__str__ wraps in class name; extract real path
        if hasattr(web_dir, '_paths') and web_dir._paths:
            web_path = str(web_dir._paths[0])
        else:
            web_path = str(web_dir)
        if os.path.isdir(web_path) and os.listdir(web_path):
            index_html = os.path.join(web_path, "index.html")

            # Mount static assets (css, js, etc.)
            assets_dir = os.path.join(web_path, "assets")
            if os.path.isdir(assets_dir):
                app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

            # SPA fallback: serve index.html for non-API GET requests
            # Uses exception handler instead of catch-all route to avoid
            # 405 conflicts with API POST/DELETE endpoints
            from starlette.exceptions import HTTPException as StarletteHTTPException

            @app.exception_handler(404)
            async def spa_fallback(request, exc):
                path = request.url.path.lstrip("/")
                # Only serve SPA for GET requests to non-API paths
                if request.method == "GET" and not path.startswith("v1/"):
                    file_path = os.path.join(web_path, path)
                    if path and os.path.isfile(file_path):
                        return FileResponse(file_path)
                    return FileResponse(index_html)
                return JSONResponse(status_code=404, content={"error": "not found"})

    except Exception:
        pass

    return app
