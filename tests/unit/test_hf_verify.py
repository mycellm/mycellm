"""Download content verification against HF-published hashes.

LFS files (model weights) verify via raw sha256 (tree API lfs.oid); small
non-LFS files only have the git blob sha1 (oid). Unavailable hash info means
"unverified" (downloads must work offline); a mismatch is fatal and deletes
the file.
"""

import hashlib

import pytest

from mycellm.inference.hf_verify import (
    HashMismatchError,
    file_content_hash,
    find_tree_hash,
    verify_download,
)

TREE = [
    {"type": "directory", "path": "sub"},
    {
        "type": "file",
        "path": "model.gguf",
        "oid": "0123456789abcdef0123456789abcdef01234567",
        "size": 5,
        "lfs": {"oid": "a" * 64, "size": 5, "pointerSize": 134},
    },
    {
        "type": "file",
        "path": "config.json",
        "oid": "ce013625030ba8dba906f756967f9e9ca394464a",
        "size": 6,
    },
]


class TestFindTreeHash:
    def test_lfs_file_uses_sha256(self):
        assert find_tree_hash(TREE, "model.gguf") == ("sha256", "a" * 64)

    def test_non_lfs_file_uses_git_blob_sha1(self):
        assert find_tree_hash(TREE, "config.json") == (
            "git-sha1",
            "ce013625030ba8dba906f756967f9e9ca394464a",
        )

    def test_missing_file_returns_none(self):
        assert find_tree_hash(TREE, "nope.bin") is None

    def test_directory_entry_never_matches(self):
        assert find_tree_hash(TREE, "sub") is None


class TestFileContentHash:
    def test_sha256(self, tmp_path):
        p = tmp_path / "f.bin"
        p.write_bytes(b"hello\n")
        assert file_content_hash(p, "sha256") == hashlib.sha256(b"hello\n").hexdigest()

    def test_git_blob_sha1_matches_git_hash_object(self, tmp_path):
        # `echo hello | git hash-object --stdin` — the canonical known value.
        p = tmp_path / "f.txt"
        p.write_bytes(b"hello\n")
        assert file_content_hash(p, "git-sha1") == "ce013625030ba8dba906f756967f9e9ca394464a"

    def test_unknown_algo_raises(self, tmp_path):
        p = tmp_path / "f"
        p.write_bytes(b"x")
        with pytest.raises(ValueError):
            file_content_hash(p, "md5")


class TestVerifyDownload:
    def test_match_returns_algo(self, tmp_path):
        p = tmp_path / "f.bin"
        p.write_bytes(b"payload")
        expected = ("sha256", hashlib.sha256(b"payload").hexdigest())
        assert verify_download(p, expected, "f.bin") == "sha256"
        assert p.exists()

    def test_no_hash_available_is_unverified(self, tmp_path):
        p = tmp_path / "f.bin"
        p.write_bytes(b"payload")
        assert verify_download(p, None, "f.bin") == "unverified"
        assert p.exists()

    def test_mismatch_raises_and_deletes(self, tmp_path):
        p = tmp_path / "f.bin"
        p.write_bytes(b"tampered")
        with pytest.raises(HashMismatchError):
            verify_download(p, ("sha256", "b" * 64), "f.bin")
        assert not p.exists()
