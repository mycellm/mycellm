"""Unit tests for federation."""

import pytest
from pathlib import Path

from mycellm.federation import (
    NetworkIdentity,
    InviteToken,
    FederationManager,
)


def test_network_identity_roundtrip(tmp_path):
    identity = NetworkIdentity(
        network_id="abc123",
        network_name="test-net",
        bootstrap_addrs=["10.0.0.1:8421"],
        public=True,
    )
    path = tmp_path / "network.json"
    identity.save(path)
    loaded = NetworkIdentity.load(path)
    assert loaded.network_id == "abc123"
    assert loaded.network_name == "test-net"
    assert loaded.public is True


def test_invite_token_validity():
    token = InviteToken(network_id="net1", max_uses=2)
    assert token.is_valid
    token.uses = 2
    assert not token.is_valid


def test_invite_token_expired():
    import time
    token = InviteToken(network_id="net1", expires_at=time.time() - 100)
    assert not token.is_valid


def test_invite_token_portable():
    token = InviteToken(
        network_id="net1",
        allowed_roles=["seeder", "relay"],
    )
    portable = token.to_portable()
    assert isinstance(portable, str)
    restored = InviteToken.from_portable(portable)
    assert restored.network_id == "net1"
    assert restored.allowed_roles == ["seeder", "relay"]
    assert restored.token_id == token.token_id


def test_federation_manager_init(tmp_path):
    fm = FederationManager(tmp_path)
    pubkey = b'\x00' * 32  # dummy
    identity = fm.init_network(pubkey, network_name="test")
    assert identity.network_name == "test"
    assert len(identity.network_id) == 64  # SHA256 hex

    # Loading again should return same identity
    fm2 = FederationManager(tmp_path)
    identity2 = fm2.init_network(pubkey)
    assert identity2.network_id == identity.network_id


def test_federation_manager_tokens(tmp_path):
    from unittest.mock import MagicMock
    fm = FederationManager(tmp_path)
    fm.init_network(b'\x00' * 32, network_name="test")

    # Mock device key for signing
    mock_key = MagicMock()
    mock_key.sign.return_value = b'\x00' * 64

    token = fm.create_invite(mock_key, roles=["seeder"], max_uses=5)
    assert token.max_uses == 5
    assert token.is_valid

    tokens = fm.list_tokens()
    assert len(tokens) == 1

    # Use the invite
    assert fm.use_invite(token.token_id)
    assert fm._tokens[token.token_id].uses == 1


def test_network_identity_to_from_dict():
    d = {"network_id": "abc", "network_name": "test", "public": True, "bootstrap_addrs": ["1.2.3.4:8421"]}
    identity = NetworkIdentity.from_dict(d)
    assert identity.to_dict()["network_id"] == "abc"
    assert identity.to_dict()["public"] is True


def test_invite_unlimited_uses():
    token = InviteToken(network_id="net1", max_uses=0)
    token.uses = 9999
    assert token.is_valid  # 0 = unlimited
