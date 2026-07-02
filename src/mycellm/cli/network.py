"""mycellm network — manage the networks this node hosts and belongs to."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(invoke_without_command=False)
console = Console()


def _load_federation():
    """Load the FederationManager for an already-initialized node.

    Exits with a hint if `mycellm init` has not been run (never creates
    identity files as a side effect of a network subcommand).
    """
    from mycellm.config import get_settings
    from mycellm.federation import FederationManager
    from mycellm.identity.keys import AccountKey

    settings = get_settings()
    network_path = settings.data_dir / "federation" / "network.json"
    account_key_path = settings.keys_dir / "account.key"
    if not network_path.exists() or not account_key_path.exists():
        console.print("[red]Node not initialized — run `mycellm init` first.[/red]")
        raise typer.Exit(1)

    account_key = AccountKey.load(settings.keys_dir)
    fm = FederationManager(settings.data_dir)
    fm.init_network(account_key.public_bytes)
    return settings, account_key, fm


def _resolve_hosted(fm, prefix: str) -> str:
    """Resolve a network id prefix among networks this node hosts."""
    matches = [nid for nid in fm.host_network_ids if nid.startswith(prefix)]
    if not matches:
        console.print(f"[red]No hosted network matches '{prefix}'.[/red]")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(f"[red]Ambiguous prefix '{prefix}' — matches {len(matches)} networks.[/red]")
        raise typer.Exit(1)
    return matches[0]


@app.command("list")
def list_networks():
    """Show every network this node hosts or belongs to."""
    _, _, fm = _load_federation()

    table = Table(title="Networks", show_lines=False)
    table.add_column("Role", style="bold")
    table.add_column("Name")
    table.add_column("Network ID")
    table.add_column("Public")
    table.add_column("Trust")

    if fm.identity:
        table.add_row(
            "home", fm.identity.network_name, fm.identity.network_id[:16],
            "yes" if fm.identity.public else "no", fm.identity.trust_level,
        )
    for h in fm.hosted_networks:
        table.add_row(
            "hosted", h.network_name, h.network_id[:16],
            "yes" if h.public else "no", h.trust_level,
        )
    for m in fm.memberships:
        table.add_row("member", m.network_name, m.network_id[:16], "-", "-")

    console.print(table)


@app.command("host")
def host(
    name: Optional[str] = typer.Argument(None, help="Name for the new hosted network"),
    public: bool = typer.Option(False, "--public", help="Allow anonymous joining"),
    join_key: str = typer.Option("", "--join-key", help="Join key for members (reserved — not yet enforced)"),
    import_path: Optional[Path] = typer.Option(None, "--import", help="Import an existing network.json instead of creating (preserves network_id)"),
):
    """Host an additional network from this node.

    The running node picks up new hosted networks on restart
    (e.g. `launchctl kickstart -k .../com.mycellm.node`).
    """
    _, account_key, fm = _load_federation()

    if import_path:
        if not import_path.exists():
            console.print(f"[red]No such file: {import_path}[/red]")
            raise typer.Exit(1)
        identity = fm.import_hosted_network(import_path)
        console.print(f"[green]Imported hosted network[/green] {identity.network_name}")
    elif name:
        identity = fm.host_network(
            account_key.public_bytes, name, public=public, join_key=join_key
        )
        console.print(f"[green]Hosting network[/green] {identity.network_name}")
    else:
        console.print("[red]Provide a network NAME or --import PATH.[/red]")
        raise typer.Exit(1)

    console.print(f"  network_id: {identity.network_id}")
    console.print("  Members connect to this node's QUIC endpoint (port 8421 by default)")
    console.print("  [dim]Restart the node to start advertising it.[/dim]")


@app.command("invite")
def invite(
    network: str = typer.Option("", "--network", help="Hosted network id (prefix) — defaults to home"),
    max_uses: int = typer.Option(0, "--max-uses", help="0 = unlimited"),
    expires_hours: float = typer.Option(0, "--expires-hours", help="0 = never"),
):
    """Create a signed invite token for a network this node hosts."""
    from mycellm.identity.keys import DeviceKey

    settings, _, fm = _load_federation()
    device_key = DeviceKey.load(settings.keys_dir)

    network_id = _resolve_hosted(fm, network) if network else ""
    token = fm.create_invite(
        device_key, max_uses=max_uses, expires_hours=expires_hours,
        network_id=network_id,
    )
    console.print(f"[green]Invite created[/green] (network {token.network_id[:12]}..., "
                  f"max_uses={max_uses or '∞'}, expires={'never' if not expires_hours else f'{expires_hours}h'})")
    portable = token.to_portable()
    console.print(f"\n  token: {portable}")
    console.print(f"  url:   https://mycellm.dev/join/{portable}\n")
    console.print("  [dim]Redeem with: mycellm init --invite <token-or-url>[/dim]")


@app.command("set-key")
def set_key(
    network: str = typer.Argument(..., help="Hosted network id (prefix)"),
    key: str = typer.Argument(..., help="Join key members must present ('' to clear)"),
):
    """Set or clear the join key on a network this node hosts.

    With a key set, peers must present it in their NodeHello to be accepted
    into the network — claims without it are dropped. Restart the node to
    apply; distribute the key to members out-of-band (or via invite).
    """
    _, _, fm = _load_federation()
    network_id = _resolve_hosted(fm, network)
    fm.set_join_key(network_id, key)
    action = "cleared" if not key else "set"
    console.print(f"[green]Join key {action}[/green] for {network_id[:16]} — restart the node to apply.")
    if key:
        console.print("  [dim]Existing members without the key will lose access on their next handshake.[/dim]")


@app.command("drop")
def drop(
    network: str = typer.Argument(..., help="Hosted network id (prefix) to stop hosting"),
):
    """Stop hosting a network (home network cannot be dropped)."""
    _, _, fm = _load_federation()
    network_id = _resolve_hosted(fm, network)
    if fm.identity and network_id == fm.identity.network_id:
        console.print("[red]Cannot drop the home network.[/red]")
        raise typer.Exit(1)
    if fm.drop_hosted_network(network_id):
        console.print(f"[green]Stopped hosting[/green] {network_id[:16]} — restart the node to apply.")
    else:
        console.print("[red]Not hosting that network.[/red]")
        raise typer.Exit(1)
