"""CLI commands for account identity management."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from mycellm.cli.banner import SPORE_GREEN
from mycellm.config import get_settings

console = Console()
app = typer.Typer(no_args_is_help=True)


@app.command("create")
def account_create(
    name: str = typer.Option(None, "--name", "-n", help="Account display name"),
) -> None:
    """Generate a new master account keypair."""
    from mycellm.identity.keys import generate_account_key

    settings = get_settings()
    settings.ensure_dirs()

    # Check if account already exists
    if (settings.keys_dir / "account.key").exists():
        console.print(f"[{SPORE_GREEN}]Account already exists.[/{SPORE_GREEN}] Use 'mycellm account show' to view.")
        raise typer.Exit(1)

    account = generate_account_key()
    account.save(settings.keys_dir)

    console.print(f"[bold {SPORE_GREEN}]Account created.[/bold {SPORE_GREEN}]")
    console.print(f"  Public key: [dim]{account.public_bytes.hex()}[/dim]")
    console.print(f"  Saved to:   [dim]{settings.keys_dir}[/dim]")


@app.command("show")
def account_show() -> None:
    """Show current account identity."""
    from mycellm.identity.keys import AccountKey

    settings = get_settings()

    if not (settings.keys_dir / "account.key").exists():
        console.print("[red]No account found.[/red] Run 'mycellm account create' first.")
        raise typer.Exit(1)

    account = AccountKey.load(settings.keys_dir)
    console.print(f"[bold]Account Public Key:[/bold] {account.public_bytes.hex()}")
    console.print(f"[bold]Keys Directory:[/bold]    {settings.keys_dir}")


@app.command("export")
def account_export(
    output: Path = typer.Option("account.pub", "--output", "-o", help="Output path for public key"),
) -> None:
    """Export account public key."""
    from mycellm.identity.keys import AccountKey

    settings = get_settings()
    account = AccountKey.load(settings.keys_dir)

    pub_hex = account.public_bytes.hex()
    output.write_text(pub_hex)
    console.print(f"Public key exported to {output}")
