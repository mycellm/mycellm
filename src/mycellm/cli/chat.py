"""CLI command: mycellm chat — interactive chat REPL."""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()
app = typer.Typer(invoke_without_command=True)


@app.callback(invoke_without_command=True)
def chat(
    model: str = typer.Option("", "--model", "-m", help="Model to chat with"),
    endpoint: str = typer.Option("http://localhost:8420", "--endpoint", "-e", help="API endpoint"),
) -> None:
    """Interactive chat REPL with streaming responses."""
    import asyncio

    asyncio.run(_chat_loop(model, endpoint))


async def _chat_loop(model: str, endpoint: str) -> None:
    import httpx

    console.print("[bold green]mycellm chat[/bold green] — type 'exit' or Ctrl+C to quit\n")

    messages: list[dict] = []

    while True:
        try:
            user_input = console.input("[bold]> [/bold]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if user_input.strip().lower() in ("exit", "quit", "/q"):
            break

        if not user_input.strip():
            continue

        messages.append({"role": "user", "content": user_input})

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{endpoint}/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": False,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            assistant_msg = data["choices"][0]["message"]["content"]
            messages.append({"role": "assistant", "content": assistant_msg})
            console.print(f"\n[green]{assistant_msg}[/green]\n")

        except httpx.ConnectError:
            console.print("[red]Cannot connect to daemon.[/red] Is 'mycellm serve' running?")
            messages.pop()
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            messages.pop()
