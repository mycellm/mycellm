"""Content verification for Hugging Face file downloads.

The HF tree API (`/api/models/{repo}/tree/{revision}?recursive=true`) exposes a
per-file content hash: LFS-tracked files (all model weights) carry the raw
sha256 in ``lfs.oid``; small non-LFS files only have the git blob sha1 in
``oid`` (sha1 of ``b"blob %d\\0" % size`` + content — not sha1 of the content).

Policy: verification is advisory when the tree API is unreachable (downloads
still work offline/behind proxies), but a hash mismatch is fatal — the file is
deleted and the download reported failed.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger("mycellm.inference")

_CHUNK = 1024 * 1024


class HashMismatchError(Exception):
    """Downloaded file content does not match the repo's published hash."""

    def __init__(self, filename: str, algo: str, expected: str, actual: str):
        self.filename, self.algo = filename, algo
        self.expected, self.actual = expected, actual
        super().__init__(
            f"{filename}: {algo} mismatch — expected {expected[:16]}…, got {actual[:16]}…"
        )


def find_tree_hash(entries: list, filename: str) -> tuple[str, str] | None:
    """Pick (algo, hexdigest) for filename out of a tree-API response."""
    for e in entries:
        if not isinstance(e, dict) or e.get("type") != "file" or e.get("path") != filename:
            continue
        lfs = e.get("lfs") or {}
        if lfs.get("oid"):
            return ("sha256", str(lfs["oid"]).lower())
        if e.get("oid"):
            return ("git-sha1", str(e["oid"]).lower())
    return None


async def fetch_expected_hash(
    repo_id: str, filename: str, *, revision: str = "main", headers: dict | None = None
) -> tuple[str, str] | None:
    """Look up the published content hash for one repo file.

    Returns (algo, hexdigest) with algo in {"sha256", "git-sha1"}, or None when
    the tree API is unreachable or the file isn't listed (verification skipped).
    """
    import httpx

    url = f"https://huggingface.co/api/models/{repo_id}/tree/{revision}?recursive=true"
    try:
        async with httpx.AsyncClient(
            timeout=30.0, follow_redirects=True, headers=headers or {}
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            entries = resp.json()
    except Exception as e:
        logger.warning(f"HF tree lookup failed for {repo_id} — skipping verification: {e}")
        return None
    if not isinstance(entries, list):
        return None
    found = find_tree_hash(entries, filename)
    if found is None:
        logger.warning(f"{filename} not in {repo_id} tree listing — skipping verification")
    return found


def file_content_hash(path: Path, algo: str) -> str:
    """Hash a finished download the way HF published it (see module doc)."""
    if algo == "sha256":
        h = hashlib.sha256()
    elif algo == "git-sha1":
        h = hashlib.sha1()
        h.update(b"blob %d\x00" % path.stat().st_size)
    else:
        raise ValueError(f"Unknown hash algo: {algo}")
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def verify_download(path: Path, expected: tuple[str, str] | None, filename: str) -> str:
    """Verify a completed download against its published hash.

    Returns the algo used ("sha256"/"git-sha1") or "unverified" when no hash
    was available. Raises HashMismatchError (and deletes the file) on mismatch.
    """
    if expected is None:
        return "unverified"
    algo, want = expected
    got = file_content_hash(path, algo)
    if got != want:
        try:
            path.unlink()
        except OSError:
            pass
        raise HashMismatchError(filename, algo, want, got)
    logger.info(f"Verified {filename} ({algo} ok)")
    return algo
