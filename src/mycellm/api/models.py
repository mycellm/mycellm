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


def _get_node_resources() -> dict:
    """Get available RAM and disk space for compatibility checks."""
    import platform
    import shutil

    ram_gb = 0.0
    disk_free_gb = 0.0

    try:
        if platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        ram_gb = int(line.split()[1]) / 1048576
                        break
        elif platform.system() == "Darwin":
            import subprocess
            result = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3)
            if result.returncode == 0:
                ram_gb = int(result.stdout.strip()) / (1024 ** 3)
    except Exception:
        pass

    try:
        from mycellm.config import get_settings
        settings = get_settings()
        model_dir = settings.model_dir or settings.data_dir / "models"
        target = model_dir if model_dir.exists() else settings.data_dir
        usage = shutil.disk_usage(str(target))
        disk_free_gb = usage.free / (1024 ** 3)
    except Exception:
        pass

    return {"ram_gb": round(ram_gb, 1), "disk_free_gb": round(disk_free_gb, 1)}


# Curated suggestions by RAM tier
_SUGGESTED_MODELS = [
    # (min_ram_gb, repo_id, description, param_b)
    (2, "TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF", "Fast tiny chat model", 1.1),
    (4, "TheBloke/Phi-2-GGUF", "Microsoft Phi-2, strong for size", 2.7),
    (6, "bartowski/Qwen2.5-3B-Instruct-GGUF", "Qwen 2.5 3B instruction-tuned", 3.0),
    (8, "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF", "Llama 3.1 8B chat", 8.0),
    (8, "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF", "Qwen 2.5 Coder 7B", 7.0),
    (12, "bartowski/gemma-2-9b-it-GGUF", "Google Gemma 2 9B", 9.0),
    (16, "bartowski/Qwen2.5-14B-Instruct-GGUF", "Qwen 2.5 14B instruction-tuned", 14.0),
    (24, "bartowski/Mistral-Small-24B-Instruct-2501-GGUF", "Mistral Small 24B", 24.0),
    (48, "bartowski/Qwen2.5-72B-Instruct-GGUF", "Qwen 2.5 72B frontier", 72.0),
]


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
            model_id = r.get("modelId", "")

            # Extract GGUF metadata if available
            gguf_meta = r.get("gguf", {})
            param_count = gguf_meta.get("total", 0)
            architecture = gguf_meta.get("architecture", "")
            context_length = gguf_meta.get("context_length", 0)

            # Extract from card data
            card = r.get("cardData", {})

            # Estimate smallest quant size: ~0.5 bytes/param for Q4_K_M
            est_min_gb = round(param_count * 0.5 / 1e9, 1) if param_count else 0
            est_ram_gb = round(est_min_gb * 1.2, 1) if est_min_gb else 0

            models.append({
                "repo_id": model_id,
                "downloads": r.get("downloads", 0),
                "likes": r.get("likes", 0),
                "tags": r.get("tags", [])[:10],
                "last_modified": r.get("lastModified", ""),
                "pipeline_tag": r.get("pipeline_tag", ""),
                "param_count": param_count,
                "param_b": round(param_count / 1e9, 1) if param_count else 0,
                "architecture": architecture,
                "context_length": context_length,
                "model_type": card.get("model_type", ""),
                "license": card.get("license", ""),
                "est_min_size_gb": est_min_gb,
                "est_min_ram_gb": est_ram_gb,
            })

        # Get node resources for compatibility info
        node_resources = _get_node_resources()

        return {
            "models": models, "query": q, "total": len(models),
            "node_ram_gb": node_resources["ram_gb"],
            "node_disk_free_gb": node_resources["disk_free_gb"],
        }

    except Exception as e:
        logger.warning(f"HuggingFace search failed: {e}")
        return {"models": [], "query": q, "error": str(e)}


@router.get("/search/{repo_id:path}/files")
async def list_repo_files(repo_id: str, request: Request):
    """List GGUF files in a HuggingFace repo with sizes and metadata."""
    import re

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Use tree API for file sizes
            tree_resp = await client.get(f"https://huggingface.co/api/models/{repo_id}/tree/main")
            tree_resp.raise_for_status()
            tree = tree_resp.json()

            # Get model metadata for param count / context
            meta_resp = await client.get(f"https://huggingface.co/api/models/{repo_id}")
            meta_resp.raise_for_status()
            meta = meta_resp.json()

        gguf_meta = meta.get("gguf", {})
        param_count = gguf_meta.get("total", 0)
        context_length = gguf_meta.get("context_length", 0)
        architecture = gguf_meta.get("architecture", "")

        resources = _get_node_resources()
        disk_free = resources["disk_free_gb"] * (1024 ** 3)
        ram_gb = resources["ram_gb"]

        files = []
        for f in tree:
            fname = f.get("path", "")
            if not fname.endswith(".gguf"):
                continue
            size = f.get("size", 0) or f.get("lfs", {}).get("size", 0)

            # Parse quantization from filename
            quant = ""
            quant_match = re.search(r'[._-](Q\d[_A-Z0-9]*|[Ff]16|[Ff]32|IQ\d[_A-Z0-9]*)', fname)
            if quant_match:
                quant = quant_match.group(1)

            # Estimate RAM needed (~1.2x file size for inference)
            est_ram_gb = round(size / (1024**3) * 1.2, 1) if size else 0

            # Warnings
            warnings = []
            if size and disk_free and size > disk_free:
                warnings.append(f"Insufficient disk space ({disk_free / (1024**3):.1f}GB free, need {size / (1024**3):.1f}GB)")
            if est_ram_gb and ram_gb and est_ram_gb > ram_gb * 0.8:
                warnings.append(f"May need more RAM ({est_ram_gb:.1f}GB needed, {ram_gb:.1f}GB available)")

            files.append({
                "filename": fname,
                "size_bytes": size,
                "size_gb": round(size / (1024**3), 2) if size else 0,
                "quant": quant,
                "est_ram_gb": est_ram_gb,
                "warnings": warnings,
            })

        # Sort: by quantization quality (Q4_K_M is popular default)
        quant_order = {"Q4_K_M": 0, "Q4_K_S": 1, "Q5_K_M": 2, "Q5_K_S": 3, "Q3_K_M": 4,
                       "Q6_K": 5, "Q8_0": 6, "Q2_K": 7, "F16": 8, "F32": 9}
        files.sort(key=lambda f: quant_order.get(f["quant"], 50))

        return {
            "repo_id": repo_id,
            "files": files,
            "param_count": param_count,
            "param_b": round(param_count / 1e9, 1) if param_count else 0,
            "context_length": context_length,
            "architecture": architecture,
            "disk_free_gb": round(disk_free / (1024**3), 1) if disk_free else 0,
            "ram_gb": round(ram_gb, 1),
        }

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

    import hashlib
    download_id = hashlib.sha256(f"{repo_id}/{filename}".encode()).hexdigest()[:16]

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
        "speed_mbps": 0.0,
        "eta_seconds": 0,
    }

    # Pass metadata for auto-load
    meta = {
        "quant": body.get("quant", ""),
        "param_count_b": body.get("param_b", 0),
        "ctx_len": body.get("context_length", 4096),
        "size_gb": body.get("size_gb", 0),
    }

    # Start download in background
    asyncio.create_task(_do_download(download_id, repo_id, filename, dest_path, node, meta))

    return {"download_id": download_id, "status": "started", "dest_path": str(dest_path)}


async def _do_download(download_id: str, repo_id: str, filename: str, dest_path: Path, node, meta: dict | None = None) -> None:
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
                last_time = time.time()
                last_bytes = 0

                with open(dest_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        info["bytes_downloaded"] = downloaded
                        info["progress"] = (downloaded / total * 100) if total > 0 else 0

                        # Update speed/ETA every ~2MB
                        now = time.time()
                        elapsed = now - last_time
                        if elapsed >= 1.0:
                            speed = (downloaded - last_bytes) / elapsed
                            info["speed_mbps"] = round(speed / (1024 * 1024), 1)
                            remaining = total - downloaded
                            info["eta_seconds"] = int(remaining / speed) if speed > 0 else 0
                            last_time = now
                            last_bytes = downloaded

        info["status"] = "complete"
        info["progress"] = 100.0
        info["completed_at"] = time.time()
        logger.info(f"Downloaded {filename} ({downloaded / 1024**3:.1f}GB)")

        # Auto-load the model with metadata
        try:
            model_name = filename.replace(".gguf", "")
            m = meta or {}
            await node.inference.load_model(
                str(dest_path),
                name=model_name,
                backend_type="llama.cpp",
                quant=m.get("quant", ""),
                ctx_len=m.get("ctx_len", 4096),
            )
            # Set extra metadata on the model info
            model_info = node.inference._model_info.get(model_name)
            if model_info:
                model_info.param_count_b = m.get("param_count_b", 0)
                if m.get("quant"):
                    model_info.quant = m["quant"]
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
    """List GGUF files available in the model directory with loaded status."""
    from mycellm.config import get_settings
    settings = get_settings()
    node = request.app.state.node
    model_dir = settings.model_dir or settings.data_dir / "models"

    loaded_names = {m.name for m in node.inference.loaded_models}

    files = []
    if model_dir.exists():
        for f in sorted(model_dir.glob("*.gguf")):
            stat = f.stat()
            model_name = f.stem
            is_loaded = model_name in loaded_names
            info = node.inference._model_info.get(model_name)
            files.append({
                "filename": f.name,
                "path": str(f),
                "model_name": model_name,
                "size_bytes": stat.st_size,
                "size_gb": round(stat.st_size / (1024**3), 2),
                "modified": stat.st_mtime,
                "loaded": is_loaded,
                "quant": info.quant if info else "",
                "param_count_b": info.param_count_b if info else 0,
                "ctx_len": info.ctx_len if info else 0,
            })

    return {"model_dir": str(model_dir), "files": files}


@router.post("/delete-file")
async def delete_model_file(request: Request):
    """Delete a GGUF model file from disk.

    Body: {"filename": "model.gguf"} or {"path": "/full/path/to/model.gguf"}
    """
    node = request.app.state.node
    body = await request.json()
    filename = body.get("filename", "")
    filepath = body.get("path", "")

    from mycellm.config import get_settings
    settings = get_settings()
    model_dir = settings.model_dir or settings.data_dir / "models"

    if filepath:
        target = Path(filepath)
    elif filename:
        target = model_dir / filename
    else:
        return {"error": "filename or path required"}

    if not target.exists():
        return {"error": f"File not found: {target.name}"}

    # Safety: only delete .gguf files within model_dir
    if not target.name.endswith(".gguf"):
        return {"error": "Can only delete .gguf files"}
    try:
        target.resolve().relative_to(model_dir.resolve())
    except ValueError:
        return {"error": "File is not in model directory"}

    # Unload if loaded
    model_name = target.stem
    if model_name in [m.name for m in node.inference.loaded_models]:
        await node.inference.unload_model(model_name)
        node.capabilities.models = node.inference.loaded_models
        await node.announce_capabilities()

    size_gb = round(target.stat().st_size / (1024 ** 3), 2)
    target.unlink()
    logger.info(f"Deleted model file: {target.name} ({size_gb}GB)")

    return {"status": "deleted", "filename": target.name, "size_gb": size_gb}


@router.get("/suggested")
async def suggested_models(request: Request):
    """Suggest models that will run well on this node."""
    resources = _get_node_resources()
    ram = resources["ram_gb"]
    disk = resources["disk_free_gb"]

    suggestions = []
    for min_ram, repo_id, desc, param_b in _SUGGESTED_MODELS:
        est_size_gb = round(param_b * 0.5, 1)  # Q4 estimate
        fits_ram = ram >= min_ram if ram else True
        fits_disk = disk >= est_size_gb if disk else True
        compatible = fits_ram and fits_disk

        suggestions.append({
            "repo_id": repo_id,
            "description": desc,
            "param_b": param_b,
            "min_ram_gb": min_ram,
            "est_size_gb": est_size_gb,
            "compatible": compatible,
        })

    return {
        "suggestions": suggestions,
        "node_ram_gb": ram,
        "node_disk_free_gb": disk,
    }
