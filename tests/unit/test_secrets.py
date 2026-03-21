"""Tests for the encrypted secrets store."""

import pytest
from pathlib import Path

from mycellm.identity.keys import generate_account_key
from mycellm.secrets import SecretStore


@pytest.fixture
def account_key():
    return generate_account_key()


@pytest.fixture
def store(tmp_path, account_key):
    return SecretStore(tmp_path / "secrets.json", account_key)


def test_set_and_get(store):
    store.set("openrouter", "sk-or-abc123")
    assert store.get("openrouter") == "sk-or-abc123"


def test_get_nonexistent(store):
    assert store.get("nope") == ""
    assert store.get("nope", "fallback") == "fallback"


def test_list_names(store):
    store.set("key1", "val1")
    store.set("key2", "val2")
    names = store.list_names()
    assert names == ["key1", "key2"]


def test_remove(store):
    store.set("temp", "val")
    assert store.remove("temp") is True
    assert store.get("temp") == ""
    assert store.remove("temp") is False


def test_has(store):
    store.set("exists", "val")
    assert store.has("exists") is True
    assert store.has("missing") is False


def test_persistence(tmp_path, account_key):
    """Secrets survive store recreation with same key."""
    path = tmp_path / "secrets.json"
    store1 = SecretStore(path, account_key)
    store1.set("api_key", "sk-secret-value")

    store2 = SecretStore(path, account_key)
    assert store2.get("api_key") == "sk-secret-value"


def test_wrong_key_cannot_decrypt(tmp_path, account_key):
    """Different account key cannot decrypt secrets."""
    path = tmp_path / "secrets.json"
    store1 = SecretStore(path, account_key)
    store1.set("sensitive", "top-secret")

    other_key = generate_account_key()
    store2 = SecretStore(path, other_key)
    assert store2.get("sensitive") == ""  # decryption fails silently


def test_file_permissions(store, tmp_path):
    store.set("key", "val")
    path = tmp_path / "secrets.json"
    assert path.exists()
    # File should be owner-only readable
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_encrypted_on_disk(store, tmp_path):
    """Raw values should not appear in the file."""
    store.set("my_secret", "super-secret-value-12345")
    content = (tmp_path / "secrets.json").read_text()
    assert "super-secret-value-12345" not in content
    # But the name is visible (only values encrypted)
    assert "my_secret" in content


def test_resolve_secret_reference(store):
    store.set("openrouter", "sk-or-real-key")
    assert store.resolve("secret:openrouter") == "sk-or-real-key"


def test_resolve_plain_value(store):
    assert store.resolve("sk-plain-key") == "sk-plain-key"


def test_resolve_missing_secret(store):
    assert store.resolve("secret:nonexistent") == ""


def test_overwrite(store):
    store.set("key", "v1")
    store.set("key", "v2")
    assert store.get("key") == "v2"


def test_empty_store(tmp_path, account_key):
    """Fresh store with no file has no secrets."""
    store = SecretStore(tmp_path / "nonexistent.json", account_key)
    assert store.list_names() == []
    assert store.get("anything") == ""


def test_multiple_secrets(store):
    """Store handles many secrets correctly."""
    for i in range(20):
        store.set(f"key-{i}", f"value-{i}")
    assert len(store.list_names()) == 20
    assert store.get("key-15") == "value-15"
