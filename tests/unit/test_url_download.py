"""Arbitrary-URL model downloads and their verification requirement.

Mirrored by the iOS node's `url` branch in HTTPServer — a model an admin
installs must be verifiable on either platform, by the same rule.
"""

from unittest.mock import MagicMock
from starlette.testclient import TestClient
from fastapi import FastAPI

from mycellm.api.models import router


def _client(tmp_path):
    app = FastAPI()
    app.include_router(router, prefix="/v1/node/models")
    node = MagicMock()
    app.state.node = node
    return TestClient(app)


def _post(client, body):
    return client.post("/v1/node/models/download", json=body)


# ⚠️ THE DIGEST REQUIREMENT IS THE POINT. Every other download is checked
# against a hash the node looks up itself (HF publishes lfs.oid). A
# caller-supplied URL has no such attestation, so without a digest this is the
# only way to put unverified weights on a node.

def test_url_without_sha256_is_rejected(tmp_path):
    r = _post(_client(tmp_path), {"url": "https://models.example/m.gguf", "filename": "m.gguf"})
    assert "sha256" in r.json().get("error", "").lower()


def test_url_with_short_sha256_is_rejected(tmp_path):
    r = _post(_client(tmp_path), {"url": "https://models.example/m.gguf",
                                  "filename": "m.gguf", "sha256": "abc123"})
    assert "sha256" in r.json().get("error", "").lower()


def test_url_with_non_hex_sha256_is_rejected(tmp_path):
    r = _post(_client(tmp_path), {"url": "https://models.example/m.gguf",
                                  "filename": "m.gguf", "sha256": "z" * 64})
    assert "sha256" in r.json().get("error", "").lower()


def test_plain_http_is_rejected(tmp_path):
    # The digest would catch a swapped file, but there is no reason to allow
    # the attempt in the first place.
    r = _post(_client(tmp_path), {"url": "http://models.example/m.gguf",
                                  "filename": "m.gguf", "sha256": "a" * 64})
    assert "https" in r.json().get("error", "").lower()


def test_a_url_that_is_only_a_host_has_no_filename(tmp_path):
    r = _post(_client(tmp_path), {"url": "https://models.example/", "sha256": "a" * 64})
    assert "filename" in r.json().get("error", "").lower()


def test_neither_repo_nor_url_is_rejected(tmp_path):
    err = _post(_client(tmp_path), {}).json().get("error", "").lower()
    assert "repo_id" in err and "url" in err


# ── MLX manifest ────────────────────────────────────────────────────────────
# An MLX model is a directory, so the admin-install form is per-file. Mirrored
# by the `files` branch in the iOS node's HTTPServer.

def _manifest(**over):
    base = {
        "name": "Qwen3-8B-4bit",
        "files": [
            {"path": "config.json", "url": "https://m.example/config.json",
             "sha256": "a" * 64, "size": 1234},
            {"path": "model.safetensors", "url": "https://m.example/model.safetensors",
             "sha256": "b" * 64, "size": 999},
        ],
    }
    base.update(over)
    return base


def test_manifest_requires_a_name(tmp_path):
    r = _post(_client(tmp_path), _manifest(name=""))
    assert "name" in r.json().get("error", "").lower()


def test_manifest_rejects_a_name_with_a_path_separator(tmp_path):
    r = _post(_client(tmp_path), _manifest(name="../escape"))
    assert "name" in r.json().get("error", "").lower()


def test_manifest_requires_https_per_file(tmp_path):
    m = _manifest()
    m["files"][0]["url"] = "http://m.example/config.json"
    assert "https" in _post(_client(tmp_path), m).json().get("error", "").lower()


def test_manifest_requires_a_digest_per_file(tmp_path):
    m = _manifest()
    m["files"][1]["sha256"] = ""
    assert "sha256" in _post(_client(tmp_path), m).json().get("error", "").lower()


def test_manifest_rejects_path_traversal(tmp_path):
    m = _manifest()
    m["files"][0]["path"] = "../../etc/passwd"
    assert "path" in _post(_client(tmp_path), m).json().get("error", "").lower()


# ⚠️ The scanner calls a directory loadable when it holds config.json and any
# .safetensors. A manifest missing either would publish something the picker
# offers and the engine cannot load — so it is refused before downloading.

def test_manifest_without_safetensors_is_refused(tmp_path):
    m = _manifest()
    m["files"] = [f for f in m["files"] if not f["path"].endswith(".safetensors")]
    assert "safetensors" in _post(_client(tmp_path), m).json().get("error", "").lower()


def test_manifest_without_config_json_is_refused(tmp_path):
    m = _manifest()
    m["files"] = [f for f in m["files"] if f["path"] != "config.json"]
    assert "config.json" in _post(_client(tmp_path), m).json().get("error", "").lower()
