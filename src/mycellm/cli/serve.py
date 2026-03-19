"""CLI command: mycellm serve — start the daemon."""

from __future__ import annotations

import typer
from rich.console import Console

from mycellm.cli.banner import print_banner

console = Console()
app = typer.Typer(invoke_without_command=True)


@app.callback(invoke_without_command=True)
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="API bind address"),
    port: int = typer.Option(8420, "--port", "-p", help="API port"),
    quic_port: int = typer.Option(8421, "--quic-port", help="QUIC transport port"),
    dht_port: int = typer.Option(8422, "--dht-port", help="DHT discovery port"),
    device: str = typer.Option("default", "--device", "-d", help="Device certificate name"),
    no_dht: bool = typer.Option(False, "--no-dht", help="Disable DHT discovery"),
) -> None:
    """Start the mycellm node daemon."""
    import asyncio

    print_banner(console)

    console.print(f"[dim]Starting daemon on {host}:{port}...[/dim]")

    from mycellm.node import MycellmNode

    node = MycellmNode(
        api_host=host,
        api_port=port,
        quic_port=quic_port,
        dht_port=dht_port,
        device_name=device,
        enable_dht=not no_dht,
    )

    try:
        asyncio.run(node.run())
    except KeyboardInterrupt:
        console.print("\n[dim]Shutting down...[/dim]")
