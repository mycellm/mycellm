"""CLI command: mycellm init — one-command setup for joining or creating a network."""

from __future__ import annotations

import re
import subprocess
import sys

import typer
from rich.console import Console

from mycellm.cli.banner import SPORE_GREEN, print_banner, styled_tag

console = Console()
app = typer.Typer(invoke_without_command=True)

# Default public bootstrap address
DEFAULT_BOOTSTRAP = "bootstrap.mycellm.dev:8421"


def _parse_invite(invite: str) -> str:
    """Extract portable token from an invite string (URL or raw token).

    Handles:
      - https://mycellm.dev/join/<token>
      - http://mycellm.dev/join/<token>
      - raw base64 portable token
    """
    url_match = re.match(r"https?://[^/]+/join/(.+)", invite.strip())
    if url_match:
        return url_match.group(1)
    return invite.strip()


@app.callback(invoke_without_command=True)
def init(
    create_network: str = typer.Option("", "--create-network", help="Create a new network with this name"),
    public: bool = typer.Option(False, "--public", help="Make the network publicly joinable (with --create-network)"),
    invite: str = typer.Option("", "--invite", "-i", help="Join via invite token or URL"),
    no_serve: bool = typer.Option(False, "--no-serve", help="Configure only, don't print serve instructions"),
    serve: bool = typer.Option(False, "--serve", help="Start daemon in background after init"),
) -> None:
    """Initialize mycellm — create identity and join the public network.

    \b
    Examples:
      mycellm init                              # Join public network
      mycellm init --create-network "My Org"    # Create private network
      mycellm init --invite <token-or-URL>      # Join via invite
      mycellm init --no-serve                   # Configure only
    """
    from mycellm.config import get_settings
    from mycellm.identity.keys import (
        AccountKey,
        generate_account_key,
        generate_device_key,
    )
    from mycellm.identity.certs import create_device_cert

    settings = get_settings()

    print_banner(console)
    console.print(f"[{SPORE_GREEN}]Initializing mycellm...[/{SPORE_GREEN}]\n")

    # 1. Ensure directories
    settings.ensure_dirs()

    # 2. Create account key if not exists
    account_key_path = settings.keys_dir / "account.key"
    if account_key_path.exists():
        console.print(f"  {styled_tag('BOOT')} Account key exists, reusing")
        account_key = AccountKey.load(settings.keys_dir)
        created_account = False
    else:
        account_key = generate_account_key()
        account_key.save(settings.keys_dir)
        console.print(f"  {styled_tag('BOOT')} Account key created")
        created_account = True

    # 3. Create device key + cert if not exists
    device_name = "default"
    device_key_path = settings.keys_dir / f"device-{device_name}.key"
    if device_key_path.exists():
        console.print(f"  {styled_tag('BOOT')} Device key exists, reusing")
    else:
        device_key = generate_device_key()
        device_key.save(settings.keys_dir, device_name)
        cert = create_device_cert(account_key, device_key, device_name=device_name)
        cert.save(settings.certs_dir)
        console.print(f"  {styled_tag('BOOT')} Device key + certificate created")

    # 4. Determine bootstrap config
    bootstrap_addr = DEFAULT_BOOTSTRAP

    if invite:
        # Parse invite token (URL or raw)
        portable_token = _parse_invite(invite)
        try:
            from mycellm.federation import InviteToken
            token = InviteToken.from_portable(portable_token)
            console.print(f"  {styled_tag('NODE')} Invite token parsed (network: {token.network_id[:12]}...)")
        except Exception as e:
            console.print(f"  [red]Failed to parse invite token: {e}[/red]")
            raise typer.Exit(1)

    if create_network:
        # Initialize as a new network (bootstrap node)
        from mycellm.federation import FederationManager
        fm = FederationManager(settings.data_dir)
        identity = fm.init_network(
            account_pubkey=account_key.public_bytes,
            network_name=create_network,
            public=public,
        )
        console.print(f"  {styled_tag('NODE')} Network created: {identity.network_name} (public={identity.public})")
        bootstrap_addr = ""  # Bootstrap nodes don't need to connect to a bootstrap

    # 5. Write bootstrap config to .env (idempotent)
    env_path = settings.config_dir / ".env"
    env_lines: dict[str, str] = {}

    # Read existing .env if present
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                env_lines[key.strip()] = val.strip()

    if bootstrap_addr and not create_network:
        env_lines["MYCELLM_BOOTSTRAP_PEERS"] = bootstrap_addr

    # Ask about telemetry (only on first init, not if .env already has it)
    if "MYCELLM_TELEMETRY" not in env_lines and not no_serve:
        console.print()
        console.print(f"  [{SPORE_GREEN}]Telemetry[/{SPORE_GREEN}]")
        console.print("  [dim]Share anonymous usage stats (request counts, TPS, model names)[/dim]")
        console.print("  [dim]with the network? No prompts, IPs, or user data. Helps the[/dim]")
        console.print("  [dim]stats page show real network activity.[/dim]")
        try:
            opt_in = typer.confirm("  Enable telemetry?", default=True)
        except (EOFError, KeyboardInterrupt):
            opt_in = False
        env_lines["MYCELLM_TELEMETRY"] = "true" if opt_in else "false"
        console.print(f"  {styled_tag('BOOT')} Telemetry {'enabled' if opt_in else 'disabled'}")
        console.print()

    if env_lines:
        env_content = "\n".join(f"{k}={v}" for k, v in env_lines.items()) + "\n"
        env_path.write_text(env_content)
        console.print(f"  {styled_tag('BOOT')} Config written to {env_path}")

    # Summary
    console.print()
    if created_account:
        pub_hex = account_key.public_bytes.hex()[:16]
        console.print(f"  Account: {pub_hex}...")
    console.print(f"  Data:    {settings.data_dir}")
    console.print(f"  Config:  {settings.config_dir}")
    if bootstrap_addr and not create_network:
        console.print(f"  Bootstrap: {bootstrap_addr}")

    console.print()
    console.print(f"[{SPORE_GREEN}]Ready.[/{SPORE_GREEN}]")

    if serve:
        console.print("\n[dim]Starting daemon in background...[/dim]")
        subprocess.Popen(
            [sys.executable, "-m", "mycellm", "serve", "--host", "0.0.0.0"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        console.print(f"[{SPORE_GREEN}]Daemon started.[/{SPORE_GREEN}]")
    elif not no_serve:
        console.print("Next steps:")
        console.print("  mycellm serve                    # Start the node")
        console.print("  mycellm serve --install-service  # Start + auto-restart on boot")
