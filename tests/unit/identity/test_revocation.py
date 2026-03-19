"""Tests for revocation list."""

from mycellm.identity.revocation import RevocationList


def test_revoke_and_check(tmp_path):
    rl = RevocationList(tmp_path / "revocations.json")
    assert not rl.is_revoked("abc123")

    rl.revoke("abc123")
    assert rl.is_revoked("abc123")


def test_revocation_persists(tmp_path):
    path = tmp_path / "revocations.json"
    rl = RevocationList(path)
    rl.revoke("deadbeef")

    rl2 = RevocationList(path)
    assert rl2.is_revoked("deadbeef")


def test_unrevoke(tmp_path):
    rl = RevocationList(tmp_path / "revocations.json")
    rl.revoke("abc123")
    assert rl.is_revoked("abc123")

    rl.unrevoke("abc123")
    assert not rl.is_revoked("abc123")
