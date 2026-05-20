"""CLI command: mycellm secret — manage encrypted API keys and tokens."""

from __future__ import annotations

import typer
from rich.console import Console

from mycellm.cli.banner import LEDGER_GOLD, styled_tag

console = Console()
app = typer.Typer(invoke_without_command=True, no_args_is_help=True)


def _get_store():
    """Load the secret store using the current account key."""
    from mycellm.config import get_settings
    from mycellm.identity.keys import AccountKey
    from mycellm.secrets import SecretStore

    settings = get_settings()
    account_key_path = settings.keys_dir / "account.key"
    if not account_key_path.exists():
        console.print("[red]No account found. Run 'mycellm init' first.[/red]")
        raise typer.Exit(1)

    account_key = AccountKey.load(settings.keys_dir)
    return SecretStore(settings.data_dir / "secrets.json", account_key)


@app.command("set")
def set_secret(
    name: str = typer.Argument(help="Secret name (e.g. 'openrouter', 'hf-token')"),
    value: str = typer.Option("", "--value", "-v", help="Secret value (prompted if omitted)"),
) -> None:
    """Store an encrypted secret.

    \b
    Examples:
      mycellm secret set openrouter -v sk-or-EXAMPLE123
      mycellm secret set hf-token              # prompts for value
    """
    if not value:
        value = typer.prompt("Secret value", hide_input=True)

    store = _get_store()
    existed = store.has(name)
    store.set(name, value)

    action = "Updated" if existed else "Stored"
    console.print(f"  {styled_tag('SECURITY')} {action} secret '{name}'")
    console.print(f"  [dim]Use in model configs: \"api_key\": \"secret:{name}\"[/dim]")


@app.command("list")
def list_secrets() -> None:
    """List all stored secret names (values are never shown)."""
    store = _get_store()
    names = store.list_names()
    if not names:
        console.print("  [dim]No secrets stored.[/dim]")
        console.print("  [dim]Add one: mycellm secret set <name> -v <value>[/dim]")
        return

    console.print(f"  [{LEDGER_GOLD}]{len(names)} secret(s):[/{LEDGER_GOLD}]")
    for name in names:
        console.print(f"    {name}")


@app.command("remove")
def remove_secret(
    name: str = typer.Argument(help="Secret name to remove"),
) -> None:
    """Remove a stored secret."""
    store = _get_store()
    if store.remove(name):
        console.print(f"  {styled_tag('SECURITY')} Removed secret '{name}'")
    else:
        console.print(f"  [dim]Secret '{name}' not found.[/dim]")


@app.command("get")
def get_secret(
    name: str = typer.Argument(help="Secret name to retrieve"),
) -> None:
    """Retrieve and display a secret value (use with caution)."""
    store = _get_store()
    value = store.get(name)
    if value:
        # Show masked by default, full with confirmation
        masked = value[:4] + "..." + value[-4:] if len(value) > 12 else "****"
        console.print(f"  {name}: {masked}")
        if typer.confirm("Show full value?", default=False):
            console.print(f"  {value}")
    else:
        console.print(f"  [dim]Secret '{name}' not found.[/dim]")
