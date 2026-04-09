"""Tests for configuration settings."""

from pathlib import Path

from mycellm.config.settings import MycellmSettings


def test_default_settings():
    s = MycellmSettings()
    assert s.api_port == 8420
    assert s.quic_port == 8421
    assert s.dht_port == 8422
    assert s.max_concurrent_inferences == 2


def test_env_override(monkeypatch):
    monkeypatch.setenv("MYCELLM_API_PORT", "9999")
    monkeypatch.setenv("MYCELLM_NODE_NAME", "test-node")
    s = MycellmSettings()
    assert s.api_port == 9999
    assert s.node_name == "test-node"


def test_bootstrap_peers_parsing():
    s = MycellmSettings(bootstrap_peers="10.0.0.1:8421,10.0.0.2:8421")
    peers = s.get_bootstrap_list()
    assert len(peers) == 2
    assert peers[0] == ("10.0.0.1", 8421)
    assert peers[1] == ("10.0.0.2", 8421)


def test_bootstrap_peers_empty():
    s = MycellmSettings(bootstrap_peers="")
    assert s.get_bootstrap_list() == []


def test_ensure_dirs(tmp_path):
    s = MycellmSettings(
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
    )
    s.ensure_dirs()
    assert s.data_dir.exists()
    assert s.keys_dir.exists()
    assert s.certs_dir.exists()
    assert s.config_dir.exists()


def test_derived_paths():
    s = MycellmSettings(data_dir=Path("/tmp/test-mycellm"))
    assert s.keys_dir == Path("/tmp/test-mycellm/keys")
    assert s.certs_dir == Path("/tmp/test-mycellm/certs")
    assert s.db_path == Path("/tmp/test-mycellm/mycellm.db")
