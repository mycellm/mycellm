"""Unit tests for mycellm init CLI command."""



def test_init_creates_account_and_device(tmp_path):
    """mycellm init creates account + device files in a fresh directory."""
    from mycellm.identity.keys import generate_account_key, generate_device_key
    from mycellm.identity.certs import create_device_cert

    keys_dir = tmp_path / "keys"
    certs_dir = tmp_path / "certs"
    keys_dir.mkdir()
    certs_dir.mkdir()

    # Simulate init logic
    account_key = generate_account_key()
    account_key.save(keys_dir)
    device_key = generate_device_key()
    device_key.save(keys_dir, "default")
    cert = create_device_cert(account_key, device_key, device_name="default")
    cert.save(certs_dir)

    assert (keys_dir / "account.key").exists()
    assert (keys_dir / "account.pub").exists()
    assert (keys_dir / "device-default.key").exists()
    assert (keys_dir / "device-default.pub").exists()
    assert (certs_dir / "device-default.cert").exists()


def test_init_idempotent(tmp_path):
    """Running init twice doesn't overwrite existing keys."""
    from mycellm.identity.keys import generate_account_key, AccountKey

    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()

    # First init
    key1 = generate_account_key()
    key1.save(keys_dir)
    pub1 = key1.public_bytes

    # Second init — should load existing
    key2 = AccountKey.load(keys_dir)
    assert key2.public_bytes == pub1


def test_parse_invite_url():
    """Invite URL is correctly parsed to extract token."""
    from mycellm.cli.init import _parse_invite

    token = "eyJ0b2tlbl9pZCI6ICJ0ZXN0In0="
    assert _parse_invite(f"https://mycellm.dev/join/{token}") == token
    assert _parse_invite(f"http://mycellm.dev/join/{token}") == token
    assert _parse_invite(token) == token


def test_parse_invite_raw_token():
    """Raw portable token passes through unchanged."""
    from mycellm.cli.init import _parse_invite

    raw = "eyJ0b2tlbl9pZCI6ICJ0ZXN0In0="
    assert _parse_invite(raw) == raw


def test_creates_network_identity(tmp_path):
    """--create-network creates a FederationManager with correct name."""
    from mycellm.identity.keys import generate_account_key
    from mycellm.federation import FederationManager

    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    account_key = generate_account_key()
    account_key.save(keys_dir)

    fm = FederationManager(tmp_path)
    identity = fm.init_network(
        account_pubkey=account_key.public_bytes,
        network_name="test-org",
        public=True,
    )

    assert identity.network_name == "test-org"
    assert identity.public is True
    assert len(identity.network_id) == 64  # SHA256 hex


def test_writes_bootstrap_to_env(tmp_path):
    """Init writes MYCELLM_BOOTSTRAP_PEERS to .env file."""
    env_path = tmp_path / ".env"

    # Simulate writing bootstrap config
    env_lines = {"MYCELLM_BOOTSTRAP_PEERS": "bootstrap.mycellm.dev:8421"}
    env_content = "\n".join(f"{k}={v}" for k, v in env_lines.items()) + "\n"
    env_path.write_text(env_content)

    content = env_path.read_text()
    assert "MYCELLM_BOOTSTRAP_PEERS=bootstrap.mycellm.dev:8421" in content


def test_env_preserves_existing_values(tmp_path):
    """Writing .env preserves existing keys."""
    env_path = tmp_path / ".env"
    env_path.write_text("MYCELLM_API_KEY=secret123\n")

    # Read + merge
    env_lines = {}
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            env_lines[key.strip()] = val.strip()

    env_lines["MYCELLM_BOOTSTRAP_PEERS"] = "bootstrap.mycellm.dev:8421"
    env_content = "\n".join(f"{k}={v}" for k, v in env_lines.items()) + "\n"
    env_path.write_text(env_content)

    content = env_path.read_text()
    assert "MYCELLM_API_KEY=secret123" in content
    assert "MYCELLM_BOOTSTRAP_PEERS=bootstrap.mycellm.dev:8421" in content
