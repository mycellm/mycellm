"""CLI commands for device certificate management."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from mycellm.cli.banner import SPORE_GREEN
from mycellm.config import get_settings

console = Console()
app = typer.Typer(no_args_is_help=True)


@app.command("create")
def device_create(
    name: str = typer.Option("default", "--name", "-n", help="Device name"),
    role: str = typer.Option("seeder", "--role", "-r", help="Device role (seeder/consumer/relay)"),
    ttl: float = typer.Option(0, "--ttl", help="Certificate TTL in seconds (0 = no expiry)"),
) -> None:
    """Generate device keypair and sign certificate with account key."""
    from mycellm.identity.keys import AccountKey, generate_device_key
    from mycellm.identity.certs import create_device_cert

    settings = get_settings()
    settings.ensure_dirs()

    # Load account
    if not (settings.keys_dir / "account.key").exists():
        console.print("[red]No account found.[/red] Run 'mycellm account create' first.")
        raise typer.Exit(1)

    account = AccountKey.load(settings.keys_dir)
    device = generate_device_key()
    device.save(settings.keys_dir, device_name=name)

    cert = create_device_cert(
        account_key=account,
        device_key=device,
        device_name=name,
        role=role,
        ttl_seconds=ttl if ttl > 0 else None,
    )
    cert.save(settings.certs_dir)

    console.print(f"[bold {SPORE_GREEN}]Device '{name}' created.[/bold {SPORE_GREEN}]")
    console.print(f"  Peer ID:    [dim]{cert.peer_id}[/dim]")
    console.print(f"  Role:       {role}")
    console.print(f"  Public key: [dim]{device.public_bytes.hex()}[/dim]")


@app.command("list")
def device_list() -> None:
    """List all device certificates."""
    import json

    settings = get_settings()

    table = Table(title="Device Certificates")
    table.add_column("Name", style="cyan")
    table.add_column("Peer ID", style="dim")
    table.add_column("Role")
    table.add_column("Status", style="green")

    for f in sorted(settings.keys_dir.glob("device-*.json")):
        meta = json.loads(f.read_text())
        device_name = meta["device_name"]

        cert_path = settings.certs_dir / f"device-{device_name}.cert"
        if cert_path.exists():
            from mycellm.identity.certs import DeviceCert

            cert = DeviceCert.load(settings.certs_dir, device_name)
            status = "revoked" if cert.revoked else ("expired" if cert.is_expired() else "valid")
            table.add_row(device_name, cert.peer_id[:16] + "...", cert.role, status)
        else:
            table.add_row(device_name, meta["public_key_hex"][:16] + "...", "?", "no cert")

    console.print(table)


@app.command("revoke")
def device_revoke(
    name: str = typer.Argument(help="Device name to revoke"),
) -> None:
    """Revoke a device certificate."""
    from mycellm.identity.revocation import RevocationList

    settings = get_settings()
    settings.ensure_dirs()

    cert_path = settings.certs_dir / f"device-{name}.cert"
    if not cert_path.exists():
        console.print(f"[red]No certificate found for device '{name}'.[/red]")
        raise typer.Exit(1)

    from mycellm.identity.certs import DeviceCert

    cert = DeviceCert.load(settings.certs_dir, name)
    revocation = RevocationList(settings.data_dir / "revocations.json")
    revocation.revoke(cert.device_pubkey.hex())

    console.print(f"[yellow]Device '{name}' revoked.[/yellow]")
