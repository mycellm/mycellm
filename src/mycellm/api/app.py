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


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Require Bearer token on API routes when MYCELLM_API_KEY is set."""

    def __init__(self, app, api_key: str):
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip auth for public paths, public stats API, and static assets
        if path in _PUBLIC_PATHS or not path.startswith("/v1"):
            return await call_next(request)
        if "/public/" in path:
            return await call_next(request)

        # Check Authorization header
        auth = request.headers.get("authorization", "")
        if auth == f"Bearer {self.api_key}":
            return await call_next(request)

        # Check x-api-key header (convenience)
        if request.headers.get("x-api-key") == self.api_key:
            return await call_next(request)

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
        app.add_middleware(ApiKeyMiddleware, api_key=settings.api_key)

    # Store node reference for route handlers
    app.state.node = node

    # API routes
    app.include_router(openai_router, prefix="/v1")
    app.include_router(node_router, prefix="/v1/node")
    app.include_router(admin_router, prefix="/v1/admin")
    app.include_router(models_router, prefix="/v1/node/models")
    app.include_router(gateway_router, prefix="/v1/public")

    # Health check (always public — includes auth_required flag for clients)
    @app.get("/health")
    async def health():
        from mycellm import __version__
        return {
            "status": "ok",
            "version": __version__,
            "peer_id": node.peer_id,
            "auth_required": bool(settings.api_key),
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
