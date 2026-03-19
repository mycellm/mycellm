"""FastAPI application factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from mycellm.api.node import router as node_router
from mycellm.api.openai import router as openai_router

if TYPE_CHECKING:
    from mycellm.node import MycellmNode


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

    # Store node reference for route handlers
    app.state.node = node

    # API routes
    app.include_router(openai_router, prefix="/v1")
    app.include_router(node_router, prefix="/v1/node")

    # Health check
    @app.get("/health")
    async def health():
        return {"status": "ok", "peer_id": node.peer_id}

    # Try to mount web dashboard static files
    try:
        from importlib.resources import files

        web_dir = files("mycellm.web")
        # Only mount if built files exist
        import os

        web_path = str(web_dir)
        if os.path.isdir(web_path) and os.listdir(web_path):
            app.mount("/", StaticFiles(directory=web_path, html=True), name="dashboard")
    except Exception:
        pass

    return app
