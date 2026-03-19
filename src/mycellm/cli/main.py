"""Main CLI entrypoint using Typer."""

from __future__ import annotations

import typer
from rich.console import Console

from mycellm.cli.account import app as account_app
from mycellm.cli.device import app as device_app
from mycellm.cli.serve import app as serve_app
from mycellm.cli.chat import app as chat_app
from mycellm.cli.status import app as status_app

console = Console()

app = typer.Typer(
    name="mycellm",
    help="Distributed LLM inference across heterogeneous hardware.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

app.add_typer(account_app, name="account", help="Manage account identity")
app.add_typer(device_app, name="device", help="Manage device certificates")
app.add_typer(serve_app, name="serve", help="Start the mycellm daemon")
app.add_typer(chat_app, name="chat", help="Interactive chat REPL")
app.add_typer(status_app, name="status", help="Show node status")


@app.callback()
def main_callback():
    """mycellm — Distributed LLM inference across heterogeneous hardware."""
    pass


if __name__ == "__main__":
    app()
