"""CLI command: mycellm menubar — macOS menu bar monitor."""

from __future__ import annotations

import platform

import typer
from rich.console import Console

console = Console()
app = typer.Typer(invoke_without_command=True)


@app.callback(invoke_without_command=True)
def menubar(
    api: str = typer.Option(
        "http://localhost:8420", "--api", "-a", help="Local node API endpoint"
    ),
) -> None:
    """Show the mushroom in the macOS menu bar: node status, models,
    activity, and credits at a glance. Management stays in the web
    dashboard — the dropdown links to it."""
    if platform.system() != "Darwin":
        console.print("[red]mycellm menubar is macOS-only.[/red]")
        raise typer.Exit(1)
    try:
        from mycellm.menubar.app import run
    except ImportError:
        console.print(
            "[yellow]The menu bar extra isn't installed.[/yellow] "
            'Install it with: [bold]pip install "mycellm\\[menubar]"[/bold]'
        )
        raise typer.Exit(1) from None
    run(api)
