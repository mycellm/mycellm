"""CLI command: mycellm status — show node status."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

console = Console()
app = typer.Typer(invoke_without_command=True)


@app.callback(invoke_without_command=True)
def status(
    endpoint: str = typer.Option("http://localhost:8420", "--endpoint", "-e", help="API endpoint"),
) -> None:
    """Show current node status (peers, credits, models, health)."""
    import asyncio

    asyncio.run(_show_status(endpoint))


async def _show_status(endpoint: str) -> None:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{endpoint}/v1/node/status")
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError:
        console.print("[red]Cannot connect to daemon.[/red] Is 'mycellm serve' running?")
        raise typer.Exit(1)

    # Node info
    console.print(f"[bold green]mycellm node[/bold green] — {data.get('node_name', 'unnamed')}")
    console.print(f"  Peer ID: [dim]{data.get('peer_id', 'unknown')}[/dim]")
    console.print(f"  Uptime:  {data.get('uptime_seconds', 0):.0f}s")
    console.print()

    # Credits
    credits = data.get("credits", {})
    console.print(
        f"  [yellow]Credits:[/yellow] {credits.get('balance', 0):.2f} "
        f"(earned: {credits.get('earned', 0):.2f}, spent: {credits.get('spent', 0):.2f})"
    )
    console.print()

    # Peers
    peers = data.get("peers", [])
    if peers:
        table = Table(title="Connected Peers")
        table.add_column("Peer ID", style="dim")
        table.add_column("Role")
        table.add_column("Models")
        table.add_column("Status", style="green")

        for p in peers:
            table.add_row(
                p.get("peer_id", "?")[:16] + "...",
                p.get("role", "?"),
                ", ".join(p.get("models", [])),
                p.get("status", "unknown"),
            )
        console.print(table)
    else:
        console.print("  [dim]No connected peers.[/dim]")

    # Models
    models = data.get("models", [])
    if models:
        console.print()
        console.print("  [bold]Loaded Models:[/bold]")
        for m in models:
            console.print(f"    - {m['name']} ({m.get('quant', '?')}) [{m.get('backend', '?')}]")
