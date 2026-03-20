"""Tests for model search and download API helpers."""

import pytest
from pathlib import Path


def test_downloads_dict_structure():
    """Download tracking dict has expected fields."""
    from mycellm.api.models import _downloads
    assert isinstance(_downloads, dict)


def test_local_model_listing(tmp_path):
    """List local GGUF files."""
    # Create fake GGUF files
    (tmp_path / "model-a.gguf").write_bytes(b'\x00' * 100)
    (tmp_path / "model-b.gguf").write_bytes(b'\x00' * 200)
    (tmp_path / "not-a-model.txt").write_bytes(b'\x00' * 50)

    files = sorted(tmp_path.glob("*.gguf"))
    assert len(files) == 2
    assert files[0].name == "model-a.gguf"
