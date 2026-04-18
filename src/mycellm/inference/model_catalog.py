"""Model catalog — fetched from mycellm.dev with bundled fallback.

The catalog enumerates known good model variants per family and per format
(MLX / GGUF). The recommender uses it to suggest the right variant for the
running platform without users hand-picking quants.

Layout: see `model_catalog.yaml` next to this module for the schema.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import yaml

logger = logging.getLogger("mycellm.catalog")

CATALOG_URL = "https://mycellm.dev/catalog.yaml"
CACHE_TTL_SECONDS = 60 * 60 * 6  # 6 hours
CACHE_FILENAME = "model_catalog.cache.yaml"


def _bundled_path() -> Path:
    return Path(__file__).parent / "model_catalog.yaml"


def _cache_path() -> Path:
    """Where to keep the fetched catalog. Falls back to /tmp if no XDG dir."""
    try:
        from mycellm.config import get_settings
        return Path(get_settings().data_dir) / CACHE_FILENAME
    except Exception:
        return Path("/tmp") / CACHE_FILENAME


_in_memory: dict | None = None
_in_memory_loaded_at: float = 0


def _parse(text: str) -> dict:
    return yaml.safe_load(text) or {}


def load_bundled() -> dict:
    """Always-available default catalog shipped with the package."""
    return _parse(_bundled_path().read_text())


async def _fetch_remote(url: str = CATALOG_URL, timeout: float = 5.0) -> dict | None:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            return _parse(r.text)
    except Exception as e:
        logger.debug(f"Remote catalog fetch failed: {e}")
        return None


async def get_catalog(force_refresh: bool = False) -> dict:
    """Return the active catalog.

    Tries in-memory cache → on-disk cache (if fresh) → remote → bundled.
    Successful remote fetches are written to the on-disk cache.
    """
    global _in_memory, _in_memory_loaded_at
    now = time.time()

    if not force_refresh and _in_memory and (now - _in_memory_loaded_at) < CACHE_TTL_SECONDS:
        return _in_memory

    cache = _cache_path()
    if not force_refresh and cache.exists():
        age = now - cache.stat().st_mtime
        if age < CACHE_TTL_SECONDS:
            try:
                cat = _parse(cache.read_text())
                if cat:
                    _in_memory = cat
                    _in_memory_loaded_at = now
                    return cat
            except Exception:
                pass

    remote = await _fetch_remote()
    if remote:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(yaml.safe_dump(remote, sort_keys=False))
        except Exception as e:
            logger.debug(f"Could not write catalog cache: {e}")
        _in_memory = remote
        _in_memory_loaded_at = now
        logger.info(
            f"Loaded model catalog from remote (version={remote.get('catalog_version','?')})"
        )
        return remote

    bundled = load_bundled()
    _in_memory = bundled
    _in_memory_loaded_at = now
    logger.info(
        f"Using bundled model catalog (version={bundled.get('catalog_version','?')}); "
        "remote fetch failed and no fresh cache"
    )
    return bundled


def get_catalog_sync() -> dict:
    """Sync wrapper for callers that can't await. Uses in-memory cache or bundled."""
    if _in_memory:
        return _in_memory
    return load_bundled()
