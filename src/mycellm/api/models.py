"""Model management API — search, download, and manage models."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Request

logger = logging.getLogger("mycellm.api.models")

router = APIRouter()

# In-memory download tracker
_downloads: dict[str, dict] = {}  # download_id -> {status, progress, ...}


@router.get("/search")
async def search_models(request: Request, q: str = "", limit: int = 20):
    """Search HuggingFace for GGUF models."""
    if not q:
        return {"models": [], "query": ""}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://huggingface.co/api/models",
                params={
                    "search": q,
                    "filter": "gguf",
                    "sort": "downloads",
                    "direction": "-1",
                    "limit": limit,
                },
            )
            resp.raise_for_status()
            results = resp.json()

        models = []
        for r in results:
            # Extract GGUF files from siblings
            gguf_files = []
            for s in r.get("siblings", []):
                fname = s.get("rfilename", "")
                if fname.endswith(".gguf"):
                    gguf_files.append(fname)

            if not gguf_files:
                continue

            # Estimate size from model ID
            model_id = r.get("modelId", "")

            models.append({
                "repo_id": model_id,
                "downloads": r.get("downloads", 0),
                "likes": r.get("likes", 0),
                "tags": r.get("tags", [])[:10],
                "gguf_files": gguf_files[:20],  # cap at 20 variants
                "last_modified": r.get("lastModified", ""),
                "pipeline_tag": r.get("pipeline_tag", ""),
            })

        return {"models": models, "query": q, "total": len(models)}

    except Exception as e:
        logger.warning(f"HuggingFace search failed: {e}")
        return {"models": [], "query": q, "error": str(e)}


@router.get("/search/{repo_id:path}/files")
async def list_repo_files(repo_id: str, request: Request):
    """List GGUF files in a HuggingFace repo with sizes."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"https://huggingface.co/api/models/{repo_id}")
            resp.raise_for_status()
            data = resp.json()

        files = []
        for s in data.get("siblings", []):
            fname = s.get("rfilename", "")
            if fname.endswith(".gguf"):
                # Get file size
                size = s.get("size", 0)
                files.append({
                    "filename": fname,
                    "size_bytes": size,
                    "size_gb": round(size / (1024**3), 2) if size else 0,
                })

        return {"repo_id": repo_id, "files": files}

    except Exception as e:
        return {"repo_id": repo_id, "files": [], "error": str(e)}


@router.post("/download")
async def download_model(request: Request):
    """Download a GGUF model from HuggingFace.

    Body: {"repo_id": "TheBloke/Llama-2-7B-GGUF", "filename": "llama-2-7b.Q4_K_M.gguf"}
    """
    node = request.app.state.node
    body = await request.json()
    repo_id = body.get("repo_id", "")
    filename = body.get("filename", "")

    if not repo_id or not filename:
        return {"error": "repo_id and filename required"}

    # Determine download path
    from mycellm.config import get_settings
    settings = get_settings()
    model_dir = settings.model_dir or settings.data_dir / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    dest_path = model_dir / filename

    if dest_path.exists():
        return {"error": f"File already exists: {filename}", "path": str(dest_path)}

    download_id = f"{repo_id}/{filename}".replace("/", "_")[:32]

    if download_id in _downloads and _downloads[download_id].get("status") == "downloading":
        return {"error": "Download already in progress", "download_id": download_id}

    _downloads[download_id] = {
        "download_id": download_id,
        "repo_id": repo_id,
        "filename": filename,
        "status": "downloading",
        "progress": 0.0,
        "bytes_downloaded": 0,
        "total_bytes": 0,
        "started_at": time.time(),
        "dest_path": str(dest_path),
    }

    # Start download in background
    asyncio.create_task(_do_download(download_id, repo_id, filename, dest_path, node))

    return {"download_id": download_id, "status": "started", "dest_path": str(dest_path)}


async def _do_download(download_id: str, repo_id: str, filename: str, dest_path: Path, node) -> None:
    """Background download task."""
    url = f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
    info = _downloads[download_id]

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=3600.0), follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                info["total_bytes"] = total
                downloaded = 0

                with open(dest_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        info["bytes_downloaded"] = downloaded
                        info["progress"] = (downloaded / total * 100) if total > 0 else 0

        info["status"] = "complete"
        info["progress"] = 100.0
        info["completed_at"] = time.time()
        logger.info(f"Downloaded {filename} ({downloaded / 1024**3:.1f}GB)")

        # Auto-load the model
        try:
            model_name = filename.replace(".gguf", "")
            await node.inference.load_model(
                str(dest_path),
                name=model_name,
                backend_type="llama.cpp",
            )
            node.capabilities.models = node.inference.loaded_models
            await node.announce_capabilities()
            info["loaded_as"] = model_name
            logger.info(f"Auto-loaded model: {model_name}")
        except Exception as e:
            info["load_error"] = str(e)
            logger.warning(f"Auto-load failed for {filename}: {e}")

    except Exception as e:
        info["status"] = "failed"
        info["error"] = str(e)
        logger.error(f"Download failed for {filename}: {e}")
        # Clean up partial file
        if dest_path.exists():
            dest_path.unlink()


@router.get("/downloads")
async def list_downloads(request: Request):
    """List active and recent downloads."""
    return {"downloads": list(_downloads.values())}


@router.get("/local")
async def list_local_models(request: Request):
    """List GGUF files available in the model directory."""
    from mycellm.config import get_settings
    settings = get_settings()
    model_dir = settings.model_dir or settings.data_dir / "models"

    files = []
    if model_dir.exists():
        for f in sorted(model_dir.glob("*.gguf")):
            stat = f.stat()
            files.append({
                "filename": f.name,
                "path": str(f),
                "size_bytes": stat.st_size,
                "size_gb": round(stat.st_size / (1024**3), 2),
                "modified": stat.st_mtime,
            })

    return {"model_dir": str(model_dir), "files": files}
