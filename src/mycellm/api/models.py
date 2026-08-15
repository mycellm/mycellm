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
_download_tasks: dict[str, asyncio.Task] = {}  # download_id -> asyncio.Task


def _safe_model_dest(model_dir: Path, filename: str) -> Path | None:
    """Resolve `filename` under `model_dir`, or None if it escapes.

    ⚠️ THE DOWNLOAD PATH HAD NO CHECK AT ALL ON THE HUGGING FACE BRANCH.
    `filename` came straight off the request body into `model_dir / filename`,
    and the worker finishes with `tmp_path.rename(dest_path)` — so a caller
    holding the node API key could write a file anywhere the node process can
    write, overwriting whatever was there. On a container running as root that
    is the whole filesystem, which makes it a privilege escalation from "manage
    this node's models" to "run code on this host". The URL branch rejected a
    "/" and so was incidentally narrower; this closes both.

    `delete-file` already did exactly this check — the containment test below is
    lifted from it rather than invented, so the two agree.

    A repo may legitimately hold the file in a subdirectory, so a relative path
    that stays inside `model_dir` is allowed; only escaping is refused.
    """
    if not filename or filename in (".", ".."):
        return None
    candidate = model_dir / filename
    try:
        candidate.resolve().relative_to(model_dir.resolve())
    except ValueError:
        return None
    return candidate


def _hf_headers() -> dict[str, str]:
    """Build HuggingFace API headers with optional auth token."""
    from mycellm.config import get_settings
    settings = get_settings()
    headers = {}
    if settings.hf_token:
        headers["Authorization"] = f"Bearer {settings.hf_token}"
    return headers


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
        async with httpx.AsyncClient(timeout=15, headers=_hf_headers()) as client:
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
    """List installable variants in a HuggingFace repo.

    Two layouts are recognised:
      - GGUF: one or more `.gguf` files at the repo root (llama.cpp backend)
      - MLX:  a directory of `.safetensors` shards + `config.json`
              (mlx-community/* convention). The whole repo is one variant.
    """
    import re

    try:
        async with httpx.AsyncClient(timeout=15, headers=_hf_headers()) as client:
            tree_resp = await client.get(f"https://huggingface.co/api/models/{repo_id}/tree/main")
            tree_resp.raise_for_status()
            tree = tree_resp.json()

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
        safetensors_files: list[dict] = []
        has_config = False

        for f in tree:
            fname = f.get("path", "")
            size = f.get("size", 0) or f.get("lfs", {}).get("size", 0)

            if fname == "config.json":
                has_config = True
                continue

            if fname.endswith(".safetensors"):
                safetensors_files.append({"filename": fname, "size": size})
                continue

            if not fname.endswith(".gguf"):
                continue

            quant = ""
            quant_match = re.search(r'[._-](Q\d[_A-Z0-9]*|[Ff]16|[Ff]32|IQ\d[_A-Z0-9]*)', fname)
            if quant_match:
                quant = quant_match.group(1)

            est_ram_gb = round(size / (1024**3) * 1.2, 1) if size else 0

            warnings = []
            if size and disk_free and size > disk_free:
                warnings.append(
                    f"Insufficient disk space ({disk_free / (1024**3):.1f}GB free, "
                    f"need {size / (1024**3):.1f}GB)"
                )
            if est_ram_gb and ram_gb and est_ram_gb > ram_gb * 0.8:
                warnings.append(
                    f"May need more RAM ({est_ram_gb:.1f}GB needed, {ram_gb:.1f}GB available)"
                )

            files.append({
                "filename": fname,
                "format": "gguf",
                "backend": "llama.cpp",
                "size_bytes": size,
                "size_gb": round(size / (1024**3), 2) if size else 0,
                "quant": quant,
                "est_ram_gb": est_ram_gb,
                "warnings": warnings,
            })

        # MLX repo detection: directory of safetensors + config.json with no .gguf
        if has_config and safetensors_files and not files:
            total_size = sum(s["size"] for s in safetensors_files if s["size"])
            est_ram_gb = round(total_size / (1024**3) * 1.2, 1) if total_size else 0
            warnings = []
            if total_size and disk_free and total_size > disk_free:
                warnings.append(
                    f"Insufficient disk space ({disk_free / (1024**3):.1f}GB free, "
                    f"need {total_size / (1024**3):.1f}GB)"
                )
            if est_ram_gb and ram_gb and est_ram_gb > ram_gb * 0.8:
                warnings.append(
                    f"May need more RAM ({est_ram_gb:.1f}GB needed, {ram_gb:.1f}GB available)"
                )
            quant = ""
            for tag in ("mxfp4", "mxfp8", "nvfp4", "4bit", "8bit", "bf16", "DWQ"):
                if tag.lower() in repo_id.lower():
                    quant = tag
                    break
            files.append({
                "filename": "",
                "format": "mlx",
                "backend": "mlx",
                "repo_id": repo_id,
                "size_bytes": total_size,
                "size_gb": round(total_size / (1024**3), 2) if total_size else 0,
                "quant": quant,
                "est_ram_gb": est_ram_gb,
                "shards": len(safetensors_files),
                "warnings": warnings,
            })

        # Quant ranking (GGUF) — keep MLX entries at top by inserting sort key
        quant_order = {"Q4_K_M": 0, "Q4_K_S": 1, "Q5_K_M": 2, "Q5_K_S": 3, "Q3_K_M": 4,
                       "Q6_K": 5, "Q8_0": 6, "Q2_K": 7, "F16": 8, "F32": 9}
        files.sort(key=lambda f: (0 if f.get("format") == "mlx" else 1,
                                  quant_order.get(f.get("quant", ""), 50)))

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
    source_url = (body.get("url") or "").strip()
    sha256 = (body.get("sha256") or "").strip().lower()

    manifest = body.get("files")
    if isinstance(manifest, list) and manifest:
        # ── MLX manifest ─────────────────────────────────────────────────
        # An MLX model is a DIRECTORY — config, tokenizer and one or more
        # safetensors shards that only mean anything together — so the
        # admin-install form is per-file, each entry naming its own URL and
        # digest.
        #
        # ⚠️ THE PYTHON NODE COULD NOT INSTALL AN MLX MODEL AT ALL BEFORE THIS.
        # /download fetches exactly one file to one path, which is the GGUF
        # shape; the iOS node has had MLXRepo for directory installs since
        # build 16. Adding the manifest here closes that asymmetry rather than
        # widening it, which matters on a fleet where every node is Apple
        # Silicon and MLX is the native format.
        name = (body.get("name") or "").strip()
        if not name or "/" in name:
            return {"error": "name required for a manifest install"}

        assets = []
        for f in manifest:
            path_ = (f.get("path") or "").strip()
            if not path_ or ".." in path_ or path_.startswith("/"):
                return {"error": "each file needs a safe relative path"}
            u = (f.get("url") or "").strip()
            if not u.lower().startswith("https://"):
                return {"error": f"{path_}: url must be an absolute https URL"}
            digest = (f.get("sha256") or "").strip().lower()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                return {"error": f"{path_}: sha256 required — 64 hex characters"}
            assets.append({"path": path_, "url": u, "sha256": digest,
                           "size": int(f.get("size") or 0)})

        # `scan_local_models` treats a directory as loadable when it holds
        # config.json and any .safetensors. A manifest missing either publishes
        # something the picker offers and the engine cannot load, so refuse it
        # before downloading rather than after.
        if not any(a["path"].endswith(".safetensors") for a in assets):
            return {"error": "manifest must include at least one .safetensors file"}
        if not any(a["path"] == "config.json" for a in assets):
            return {"error": "manifest must include config.json"}

        from mycellm.config import get_settings
        settings = get_settings()
        model_dir = settings.model_dir or settings.data_dir / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        dest_dir = model_dir / name
        if dest_dir.exists():
            return {"error": f"Model already exists: {name}", "path": str(dest_dir)}

        import hashlib as _h
        download_id = _h.sha256(f"manifest:{name}".encode()).hexdigest()[:16]
        if download_id in _downloads and _downloads[download_id].get("status") == "downloading":
            return {"error": "Download already in progress", "download_id": download_id}

        total = sum(a["size"] for a in assets)
        _downloads[download_id] = {
            "download_id": download_id, "repo_id": "manifest", "filename": name,
            "status": "downloading", "progress": 0.0, "bytes_downloaded": 0,
            "total_bytes": total, "started_at": time.time(),
            "dest_path": str(dest_dir), "speed_mbps": 0.0, "eta_seconds": 0,
            "format": "mlx",
        }
        task = asyncio.create_task(_do_manifest_download(download_id, assets, dest_dir, node))
        _download_tasks[download_id] = task
        return {"download_id": download_id, "status": "started", "name": name,
                "format": "mlx", "files": len(assets), "total_bytes": total,
                "dest_path": str(dest_dir)}

    if source_url:
        # Arbitrary URL: for models that aren't on Hugging Face — an internal
        # mirror, a private build, an admin coordinating a fleet install.
        #
        # ⚠️ `sha256` IS REQUIRED HERE AND NOWHERE ELSE. Every other download is
        # checked against a hash the node looks up itself (HF publishes
        # `lfs.oid`), so it can always tell whether it got the file it asked
        # for. A caller-supplied URL has no such attestation; without a digest
        # this would be the only way to place unverified weights on a node, on
        # the say-so of whoever holds the API key. Requiring the caller to
        # commit to a hash up front preserves the invariant that everything on
        # disk was verified against something stated in advance.
        if not source_url.lower().startswith("https://"):
            return {"error": "url must be an absolute https URL"}
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            return {
                "error": "sha256 required for url downloads — "
                         "64 hex characters of the file's digest"
            }
        if not filename:
            filename = source_url.rsplit("/", 1)[-1].split("?")[0]
        if not filename or "/" in filename:
            return {"error": "filename required (or a URL ending in one)"}
    elif not repo_id or not filename:
        return {"error": "repo_id and filename, url and sha256, or files (manifest), required"}

    # Determine download path
    from mycellm.config import get_settings
    settings = get_settings()
    model_dir = settings.model_dir or settings.data_dir / "models"
    model_dir.mkdir(parents=True, exist_ok=True)

    dest_path = _safe_model_dest(model_dir, filename)
    if dest_path is None:
        return {"error": "filename must resolve inside the models directory"}

    if dest_path.exists():
        return {"error": f"File already exists: {filename}", "path": str(dest_path)}

    import hashlib
    key = f"{source_url}#{filename}" if source_url else f"{repo_id}/{filename}"
    download_id = hashlib.sha256(key.encode()).hexdigest()[:16]

    if download_id in _downloads and _downloads[download_id].get("status") == "downloading":
        return {"error": "Download already in progress", "download_id": download_id}

    _downloads[download_id] = {
        "download_id": download_id,
        "repo_id": repo_id or (source_url.split("/")[2] if source_url else ""),
        "filename": filename,
        "source_url": source_url,
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
    task = asyncio.create_task(_do_download(
        download_id, repo_id, filename, dest_path, node, meta,
        source_url=source_url or None, sha256=sha256 or None))
    _download_tasks[download_id] = task

    return {
        "download_id": download_id,
        "status": "started",
        "dest_path": str(dest_path),
        **({"url": source_url, "sha256": sha256} if source_url else {"repo_id": repo_id}),
    }


async def _do_download(
    download_id: str, repo_id: str, filename: str, dest_path: Path, node,
    meta: dict | None = None, source_url: str | None = None, sha256: str | None = None,
) -> None:
    """Background download task."""
    url = source_url or f"https://huggingface.co/{repo_id}/resolve/main/{filename}"
    info = _downloads[download_id]

    from mycellm.inference.hf_verify import fetch_expected_hash, verify_download

    # A repo subdirectory is a legal destination (it stays inside model_dir),
    # but nothing has created it yet — without this the write fails at the
    # last moment, after the bytes are already on disk.
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    try:
        if source_url:
            # Not advisory: there is no second source to fall back on, so an
            # unverifiable URL download is a failed one.
            expected_hash = ("sha256", sha256)
        else:
            expected_hash = await fetch_expected_hash(repo_id, filename, headers=_hf_headers())
        # ⚠️ NO HUGGING FACE CREDENTIALS ON A THIRD-PARTY URL. `_hf_headers()`
        # carries the node's HF token; sending it to whatever host an admin
        # names would hand that token to a server that has no business seeing
        # it — and to any host it redirects to.
        request_headers = _hf_headers() if not source_url else {}
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, read=3600.0),
            follow_redirects=True,
            headers=request_headers,
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                info["total_bytes"] = total
                downloaded = 0
                last_time = time.time()
                last_bytes = 0

                with open(tmp_path, "wb") as f:
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

        info["verified"] = verify_download(tmp_path, expected_hash, filename)
        tmp_path.rename(dest_path)

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
            node.capabilities.role = "seeder" if node.inference.loaded_models else "consumer"
            await node.announce_capabilities()
            info["loaded_as"] = model_name
            logger.info(f"Auto-loaded model: {model_name}")
        except Exception as e:
            info["load_error"] = str(e)
            logger.warning(f"Auto-load failed for {filename}: {e}")

    except asyncio.CancelledError:
        info["status"] = "aborted"
        logger.info(f"Download aborted: {filename}")
        tmp_path.unlink(missing_ok=True)
        if dest_path.exists():
            dest_path.unlink(missing_ok=True)
    except Exception as e:
        info["status"] = "failed"
        info["error"] = str(e)
        logger.error(f"Download failed for {filename}: {e}")
        tmp_path.unlink(missing_ok=True)
        if dest_path.exists():
            dest_path.unlink(missing_ok=True)
    finally:
        _download_tasks.pop(download_id, None)


@router.get("/downloads")
async def list_downloads(request: Request):
    """List active and recent downloads."""
    return {"downloads": list(_downloads.values())}


@router.post("/downloads/abort")
async def abort_download(request: Request):
    """Abort an in-progress download."""
    body = await request.json()
    download_id = body.get("download_id", "")
    if not download_id or download_id not in _downloads:
        return {"error": "Unknown download_id"}

    info = _downloads[download_id]
    if info.get("status") != "downloading":
        return {"error": "Download not in progress", "status": info.get("status")}

    task = _download_tasks.get(download_id)
    if task and not task.done():
        task.cancel()

    info["status"] = "aborted"

    # Clean up partial file
    dest = info.get("dest_path", "")
    if dest:
        from pathlib import Path
        p = Path(dest)
        if p.exists():
            p.unlink(missing_ok=True)

    return {"status": "aborted", "download_id": download_id}


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
        node.capabilities.role = "seeder" if node.inference.loaded_models else "consumer"
        await node.announce_capabilities()

    size_gb = round(target.stat().st_size / (1024 ** 3), 2)
    target.unlink()
    logger.info(f"Deleted model file: {target.name} ({size_gb}GB)")

    return {"status": "deleted", "filename": target.name, "size_gb": size_gb}


@router.get("/suggested")
async def suggested_models(request: Request, role: str = "", limit: int = 10):
    """Suggest models that will run well on this node.

    Uses the live mycellm catalog (fetched from mycellm.dev with bundled
    fallback) and the host's platform/RAM/disk to rank variants. On Apple
    Silicon prefers MLX; on Linux/Windows prefers GGUF.
    """
    from mycellm.inference.model_catalog import get_catalog
    from mycellm.inference.recommender import recommend, detect_platform

    node = request.app.state.node
    resources = _get_node_resources()
    ram = resources["ram_gb"]
    disk = resources["disk_free_gb"]

    # Account for currently-loaded models when computing free RAM.
    loaded_bytes = 0
    try:
        for cap in node.inference.loaded_models:
            loaded_bytes += int(getattr(cap, "loaded_bytes", 0) or 0)
    except Exception:
        pass
    free_ram = max(0.0, ram - loaded_bytes / (1024**3))

    catalog = await get_catalog()
    recs = recommend(
        catalog,
        free_ram_gb=free_ram,
        free_disk_gb=disk,
        role_filter=role or None,
        limit=limit,
    )

    return {
        "suggestions": [r.to_dict() for r in recs],
        "platform": detect_platform(),
        "node_ram_gb": ram,
        "node_free_ram_gb": round(free_ram, 1),
        "node_disk_free_gb": disk,
        "loaded_bytes": loaded_bytes,
        "catalog_version": catalog.get("catalog_version", ""),
    }


async def _do_manifest_download(download_id: str, assets: list[dict], dest_dir: Path, node) -> None:
    """Fetch every file of an MLX model, then publish the directory in one move.

    Mirrors MLXRepo on the iOS node, and for the same reasons:

    1. **Publish atomically.** A directory is "a loadable model" the moment it
       holds config.json and any .safetensors, so writing in place would make an
       interrupted download look installed — offered by the picker and failing
       at load. Assemble in `.staging/` and rename once every file is present.
    2. **Stage on the same volume**, so publishing is a rename rather than a
       multi-gigabyte copy needing the model's size twice.
    3. **Verify per file, not at the end.** There is no tree API to fall back on
       here, so the caller-supplied digest is the only evidence the bytes are
       right — and checking each shard as it lands fails a bad one before the
       next multi-gigabyte fetch starts.
    """
    from mycellm.inference.hf_verify import file_content_hash

    info = _downloads[download_id]
    staging = dest_dir.parent / ".staging" / dest_dir.name
    staging.mkdir(parents=True, exist_ok=True)

    downloaded_total = 0
    try:
        # No credentials on third-party hosts — see the note in _do_download.
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, read=3600.0), follow_redirects=True
        ) as client:
            for asset in assets:
                target = staging / asset["path"]
                target.parent.mkdir(parents=True, exist_ok=True)

                if target.exists() and asset["size"] and target.stat().st_size == asset["size"]:
                    downloaded_total += asset["size"]
                    continue

                async with client.stream("GET", asset["url"]) as resp:
                    resp.raise_for_status()
                    with open(target, "wb") as fh:
                        async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                            fh.write(chunk)
                            downloaded_total += len(chunk)
                            info["bytes_downloaded"] = downloaded_total
                            if info["total_bytes"]:
                                info["progress"] = downloaded_total / info["total_bytes"] * 100

                got = file_content_hash(target, "sha256")
                if got != asset["sha256"]:
                    target.unlink(missing_ok=True)
                    raise ValueError(
                        f"{asset['path']} does not match the digest supplied for it "
                        f"(expected {asset['sha256'][:12]}…, got {got[:12]}…)"
                    )

        dest_dir.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(dest_dir)
        info["status"] = "completed"
        info["progress"] = 100.0
        logger.info("Manifest install complete: %s", dest_dir)
    except Exception as e:  # noqa: BLE001 - surfaced to the caller via _downloads
        info["status"] = "failed"
        info["error"] = str(e)
        logger.error("Manifest install failed for %s: %s", dest_dir.name, e)
