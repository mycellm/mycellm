"""Tests for multi-network hosting — a node hosting networks beyond its home."""

import json

import pytest

from mycellm.federation import FederationManager, NetworkIdentity
from mycellm.identity.keys import generate_device_key

PUBKEY = b"\x01" * 32


def _fm(tmp_path):
    fm = FederationManager(tmp_path)
    fm.init_network(PUBKEY, network_name="home-net")
    return fm


def test_host_network_id_derivation(tmp_path):
    fm = _fm(tmp_path)
    hosted = fm.host_network(PUBKEY, "lab")
    assert len(hosted.network_id) == 64
    assert hosted.network_id != fm.network_id  # never collides with home
    # Deterministic: same account + name → same id
    fm2 = FederationManager(tmp_path)
    fm2.init_network(PUBKEY)
    again = fm2.host_network(PUBKEY, "lab")
    assert again.network_id == hosted.network_id
    # Different name → different id
    other = fm.host_network(PUBKEY, "lab2")
    assert other.network_id != hosted.network_id


def test_host_network_persists_and_reloads(tmp_path):
    fm = _fm(tmp_path)
    hosted = fm.host_network(PUBKEY, "lab", join_key="sekrit")
    path = tmp_path / "federation" / "hosted" / f"{hosted.network_id[:16]}.json"
    assert path.exists()

    fm2 = FederationManager(tmp_path)
    fm2.init_network(PUBKEY)
    assert hosted.network_id in fm2.host_network_ids
    reloaded = fm2.hosted_networks[0]
    assert reloaded.network_name == "lab"
    assert reloaded.join_key == "sekrit"


def test_join_key_omitted_when_empty(tmp_path):
    fm = _fm(tmp_path)
    hosted = fm.host_network(PUBKEY, "lab")
    path = tmp_path / "federation" / "hosted" / f"{hosted.network_id[:16]}.json"
    assert "join_key" not in json.loads(path.read_text())


def test_network_ids_include_hosted(tmp_path):
    fm = _fm(tmp_path)
    hosted = fm.host_network(PUBKEY, "lab")
    fm.join_network("f" * 64, network_name="someone-elses")
    assert fm.network_ids == [fm.network_id, hosted.network_id, "f" * 64]
    assert fm.host_network_ids == [fm.network_id, hosted.network_id]


def test_import_hosted_network_preserves_id(tmp_path):
    # An identity created by a *different* process/account (the old coordinator)
    foreign = NetworkIdentity(network_id="c" * 64, network_name="mijkal-lab")
    src = tmp_path / "coordinator-network.json"
    foreign.save(src)

    fm = _fm(tmp_path)
    imported = fm.import_hosted_network(src)
    assert imported.network_id == "c" * 64
    assert imported.network_name == "mijkal-lab"
    assert "c" * 64 in fm.host_network_ids


def test_import_home_network_refused(tmp_path):
    fm = _fm(tmp_path)
    src = tmp_path / "federation" / "network.json"
    with pytest.raises(ValueError):
        fm.import_hosted_network(src)


def test_drop_hosted_network(tmp_path):
    fm = _fm(tmp_path)
    hosted = fm.host_network(PUBKEY, "lab")
    assert fm.drop_hosted_network(hosted.network_id) is True
    assert hosted.network_id not in fm.host_network_ids
    path = tmp_path / "federation" / "hosted" / f"{hosted.network_id[:16]}.json"
    assert not path.exists()
    assert fm.drop_hosted_network(hosted.network_id) is False


def test_home_model_visible_to_hosted_network(tmp_path):
    fm = _fm(tmp_path)
    hosted = fm.host_network(PUBKEY, "lab")
    # Members of a network this node hosts can use home-scoped models
    assert fm.is_model_visible("m", "home", [], hosted.network_id) is True
    assert fm.is_model_visible("m", "home", [], fm.network_id) is True
    # ...but the public network / unknown networks cannot
    assert fm.is_model_visible("m", "home", [], "d" * 64) is False
    # Explicit scopes unchanged
    assert fm.is_model_visible("m", "public", [], "d" * 64) is True
    assert fm.is_model_visible("m", "networks", ["d" * 64], "d" * 64) is True
    assert fm.is_model_visible("m", "networks", [], hosted.network_id) is False


def test_invite_for_hosted_network(tmp_path):
    fm = _fm(tmp_path)
    hosted = fm.host_network(PUBKEY, "lab")
    device_key = generate_device_key()

    token = fm.create_invite(device_key, network_id=hosted.network_id, max_uses=3)
    assert token.network_id == hosted.network_id
    assert token.verify(device_key.public_bytes)

    ok, err = fm.validate_invite(token.to_portable(), device_key.public_bytes)
    assert ok, err


def test_invite_for_unhosted_network_rejected(tmp_path):
    fm = _fm(tmp_path)
    device_key = generate_device_key()
    with pytest.raises(ValueError):
        fm.create_invite(device_key, network_id="e" * 64)


def test_cli_network_list_and_host(tmp_path, monkeypatch):
    """Smoke test the `mycellm network` CLI against a temp identity."""
    from typer.testing import CliRunner
    from mycellm.cli.main import app as cli_app
    from mycellm.identity.keys import generate_account_key

    monkeypatch.setenv("MYCELLM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MYCELLM_CONFIG_DIR", str(tmp_path / "config"))
    from mycellm.config import get_settings
    get_settings.cache_clear()
    settings = get_settings()
    settings.ensure_dirs()

    account_key = generate_account_key()
    account_key.save(settings.keys_dir)
    fm = FederationManager(settings.data_dir)
    fm.init_network(account_key.public_bytes, network_name="home-net")

    runner = CliRunner()
    try:
        result = runner.invoke(cli_app, ["network", "host", "lab"])
        assert result.exit_code == 0, result.output
        assert "Hosting network" in result.output

        result = runner.invoke(cli_app, ["network", "list"])
        assert result.exit_code == 0, result.output
        assert "lab" in result.output
        assert "hosted" in result.output
    finally:
        get_settings.cache_clear()
