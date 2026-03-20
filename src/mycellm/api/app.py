"""FastAPI application factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from mycellm.api.admin import router as admin_router
from mycellm.api.node import router as node_router
from mycellm.api.openai import router as openai_router

if TYPE_CHECKING:
    from mycellm.node import MycellmNode


# Paths that never require auth
_PUBLIC_PATHS = {"/health", "/docs", "/openapi.json"}
_PUBLIC_PREFIXES = ("/health",)  # dashboard static files served at /


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Require Bearer token on API routes when MYCELLM_API_KEY is set."""

    def __init__(self, app, api_key: str):
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip auth for public paths and static assets
        if path in _PUBLIC_PATHS or not path.startswith("/v1"):
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

    # Health check (always public — includes auth_required flag for clients)
    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "peer_id": node.peer_id,
            "auth_required": bool(settings.api_key),
        }

    # Try to mount web dashboard static files
    try:
        from importlib.resources import files
        import os

        web_dir = files("mycellm.web")
        web_path = str(web_dir)
        if os.path.isdir(web_path) and os.listdir(web_path):
            app.mount("/", StaticFiles(directory=web_path, html=True), name="dashboard")
    except Exception:
        pass

    return app
