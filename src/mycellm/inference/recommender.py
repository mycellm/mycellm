"""Platform-aware model recommender.

Given the live model catalog and the host's hardware, produces a ranked list
of recommended variants — preferring native MLX on Apple Silicon, picking
GGUF quants that fit available RAM, hiding variants that need an unavailable
backend.
"""

from __future__ import annotations

import logging
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("mycellm.recommender")


# Format → backend type expected by InferenceManager
FORMAT_BACKEND = {"mlx": "mlx", "gguf": "llama.cpp"}


def _resolve_backend(fmt: str, modalities: list[str]) -> str:
    """Map a variant's format + family modalities to an InferenceManager backend.

    A vision-language family (declares "image") in mlx format runs on the
    single-stream mlx-vlm backend. The manager also auto-detects this from
    config.json at load time, but the catalog can't read a not-yet-downloaded
    config, so we surface the right backend here for /suggested + auto-load.
    """
    backend = FORMAT_BACKEND.get(fmt, fmt)
    if fmt == "mlx" and "image" in modalities:
        return "mlx-vlm"
    return backend


def detect_platform() -> str:
    """Return the catalog `platforms` token for this host."""
    sys = platform.system()
    arch = platform.machine().lower()
    if sys == "Darwin" and arch == "arm64":
        return "darwin-arm64"
    if sys == "Darwin":
        return "darwin-x86_64"
    if sys == "Linux" and arch in ("x86_64", "amd64"):
        return "linux-x86_64"
    if sys == "Linux" and arch in ("aarch64", "arm64"):
        return "linux-aarch64"
    if sys == "Windows":
        return "win-x86_64"
    return f"{sys.lower()}-{arch}"


def _format_preference(plat: str, fmt: str) -> float:
    """Multiplier favouring the native format for the platform."""
    if plat == "darwin-arm64" and fmt == "mlx":
        return 1.30
    if plat.startswith("linux") and fmt == "gguf":
        return 1.10
    return 1.0


def _detect_resources() -> dict:
    """Hardware reading shared with /v1/node/models/suggested."""
    ram_gb = 0.0
    disk_free_gb = 0.0
    try:
        if platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        ram_gb = int(line.split()[1]) / (1024 * 1024)
                        break
        elif platform.system() == "Darwin":
            import subprocess
            r = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode == 0:
                ram_gb = int(r.stdout.strip()) / (1024**3)
    except Exception:
        pass

    try:
        target = Path.home()
        usage = shutil.disk_usage(str(target))
        disk_free_gb = usage.free / (1024**3)
    except Exception:
        pass

    return {"ram_gb": round(ram_gb, 1), "disk_free_gb": round(disk_free_gb, 1)}


@dataclass
class Recommendation:
    family: str
    description: str
    repo: str
    file: str
    format: str
    backend: str
    quant: str
    size_gb: float
    ram_gb: float
    param_count_b: float
    active_param_count_b: float
    roles: list[str]
    modalities: list[str]
    score: float
    why: str
    fits_ram: bool
    fits_disk: bool

    def to_dict(self) -> dict:
        return self.__dict__


def recommend(
    catalog: dict,
    *,
    free_ram_gb: float | None = None,
    free_disk_gb: float | None = None,
    role_filter: str | None = None,
    plat: str | None = None,
    limit: int = 10,
) -> list[Recommendation]:
    """Rank variants for the local platform.

    `free_ram_gb` should account for currently-loaded models — the manager
    can pass total_ram - loaded_bytes/1e9 for an accurate headroom check.
    """
    plat = plat or detect_platform()
    if free_ram_gb is None or free_disk_gb is None:
        res = _detect_resources()
        if free_ram_gb is None:
            free_ram_gb = res["ram_gb"]
        if free_disk_gb is None:
            free_disk_gb = res["disk_free_gb"]

    HEADROOM_GB = 4.0  # leave room for OS + KV cache
    out: list[Recommendation] = []

    for fam in catalog.get("models", []):
        if role_filter and role_filter not in fam.get("roles", []):
            continue

        family_name = fam.get("family", "")
        modalities = list(fam.get("modalities", ["text"]))
        for v in fam.get("variants", []):
            platforms = v.get("platforms", [])
            if plat not in platforms:
                continue

            fmt = v.get("format", "")
            backend = _resolve_backend(fmt, modalities)
            ram_needed = float(v.get("ram_gb", 0))
            size = float(v.get("size_gb", 0))

            fits_ram = (free_ram_gb - HEADROOM_GB) >= ram_needed
            fits_disk = free_disk_gb >= size

            base_score = _format_preference(plat, fmt)
            if not fits_ram:
                base_score *= 0.4
            if not fits_disk:
                base_score *= 0.5
            # Among viable variants, prefer larger / more capable models
            if fits_ram and fits_disk:
                import math
                pc = float(fam.get("param_count_b", 0))
                if pc > 0:
                    base_score += 0.10 * math.log10(pc + 1)

            why_parts = []
            if "image" in modalities:
                why_parts.append("vision (image input)")
            if plat == "darwin-arm64" and fmt == "mlx":
                why_parts.append("native MLX on Apple Silicon")
            elif fmt == "gguf":
                why_parts.append("portable GGUF (llama.cpp)")
            if not fits_ram:
                why_parts.append(
                    f"⚠ needs {ram_needed:.0f}GB RAM, {free_ram_gb:.0f}GB free"
                )
            if not fits_disk:
                why_parts.append(
                    f"⚠ needs {size:.0f}GB disk, {free_disk_gb:.0f}GB free"
                )

            out.append(
                Recommendation(
                    family=family_name,
                    description=fam.get("description", ""),
                    repo=v.get("repo", ""),
                    file=v.get("file", ""),
                    format=fmt,
                    backend=backend,
                    quant=v.get("quant", ""),
                    size_gb=size,
                    ram_gb=ram_needed,
                    param_count_b=float(fam.get("param_count_b", 0)),
                    active_param_count_b=float(fam.get("active_param_count_b", 0)),
                    roles=list(fam.get("roles", [])),
                    modalities=modalities,
                    score=round(base_score, 3),
                    why="; ".join(why_parts) or "available",
                    fits_ram=fits_ram,
                    fits_disk=fits_disk,
                )
            )

    out.sort(key=lambda r: r.score, reverse=True)
    return out[:limit]
