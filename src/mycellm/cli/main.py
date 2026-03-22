"""Main CLI entrypoint using Typer.

mycellm — Distributed LLM inference across heterogeneous hardware.

A peer-to-peer network for running LLM inference across GPUs worldwide.
Nodes contribute compute, earn credits, and serve models via an
OpenAI-compatible API.

Quick start:
    mycellm init                    Initialize and join the public network
    mycellm serve                   Start the node daemon
    mycellm chat                    Interactive chat with streaming + slash commands
    mycellm status                  Show node health, peers, models, credits

API usage (OpenAI-compatible):
    OPENAI_BASE_URL=http://localhost:8420/v1 python my_app.py

Relay (use external devices as inference backends):
    mycellm serve --relay http://ipad.lan:8080   Relay to an iPad/phone/Ollama

Management:
    mycellm account create          Create account identity
    mycellm device create           Create device certificate
    mycellm secret set <name>       Store encrypted API key
    mycellm serve --install-service Install as system service

For more: https://docs.mycellm.dev
"""

from __future__ import annotations

import typer
from rich.console import Console

from mycellm.cli.account import app as account_app
from mycellm.cli.device import app as device_app
from mycellm.cli.init import app as init_app
from mycellm.cli.secret import app as secret_app
from mycellm.cli.serve import app as serve_app
from mycellm.cli.chat import app as chat_app
from mycellm.cli.status import app as status_app

console = Console()

app = typer.Typer(
    name="mycellm",
    help="""[bold green]mycellm[/bold green] — Distributed LLM inference across heterogeneous hardware.

    A peer-to-peer network for running LLM inference across GPUs worldwide.
    Nodes contribute compute, earn credits, and serve models via an
    OpenAI-compatible API at /v1/chat/completions.

    [bold]Quick start:[/bold]
      mycellm init          Join the public network (creates identity + config)
      mycellm serve         Start the node daemon
      mycellm chat          Interactive chat REPL with /commands

    [bold]Environment variables:[/bold]
      MYCELLM_API_KEY         API key for node authentication
      MYCELLM_BOOTSTRAP_PEERS Bootstrap node addresses (host:port)
      MYCELLM_HF_TOKEN        HuggingFace token for gated models
      MYCELLM_DB_URL          Database URL (default: SQLite)
      MYCELLM_TELEMETRY       Opt-in anonymous usage stats (true/false)
      MYCELLM_LOG_LEVEL       Log verbosity (DEBUG/INFO/WARNING/ERROR)
      MYCELLM_RELAY_BACKENDS  Relay endpoints (comma-separated OpenAI-compatible URLs)
      MYCELLM_MODEL_DIR       Model download directory
      MYCELLM_DATA_DIR        Data directory (~/.local/share/mycellm)
      MYCELLM_CONFIG_DIR      Config directory (~/.config/mycellm)

    [bold]As an LLM backend (OpenAI-compatible):[/bold]
      OPENAI_BASE_URL=http://localhost:8420/v1
      OPENAI_API_KEY=your-key
    """,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

app.add_typer(init_app, name="init", help="Initialize mycellm — create identity and join a network")
app.add_typer(serve_app, name="serve", help="Start the mycellm node daemon")
app.add_typer(chat_app, name="chat", help="Interactive chat REPL with streaming and /commands")
app.add_typer(status_app, name="status", help="Show node status, peers, models, and credits")
app.add_typer(account_app, name="account", help="Manage account master keys (create/show/export)")
app.add_typer(device_app, name="device", help="Manage device certificates (create/list/revoke)")
app.add_typer(secret_app, name="secret", help="Manage encrypted API keys (set/list/get/remove)")


def _version_callback(value: bool):
    if value:
        from mycellm import __version__
        console.print(f"mycellm {__version__}")
        console.print(f"Copyright 2026 Michael Gifford-Santos. Apache 2.0 License.")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(False, "--version", "-v", "-V", help="Show version and exit", callback=_version_callback, is_eager=True),
):
    """mycellm — Distributed LLM inference across heterogeneous hardware."""
    pass


if __name__ == "__main__":
    app()
