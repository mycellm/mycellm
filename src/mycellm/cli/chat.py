"""CLI command: mycellm chat — interactive chat REPL with slash commands.

An interactive terminal for chatting with models on the mycellm network
and managing your node — inspired by Claude Code.

Features:
  - Streaming responses with Rich markdown rendering
  - Slash commands for node/fleet/model management
  - Auto-selects best available model (or specify with --model)
  - Multi-turn conversation context
  - ASCII mushroom branding
"""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()
app = typer.Typer(invoke_without_command=True)


@app.callback(invoke_without_command=True)
def chat(
    model: str = typer.Option("", "--model", "-m", help="Model to use (default: auto)"),
    endpoint: str = typer.Option("http://localhost:8420", "--endpoint", "-e", help="API endpoint"),
    api_key: str = typer.Option("", "--api-key", "-k", help="API key (or set MYCELLM_API_KEY)"),
) -> None:
    """Interactive chat REPL with streaming and slash commands."""
    import asyncio
    import os

    key = api_key or os.environ.get("MYCELLM_API_KEY", "")
    asyncio.run(_chat_loop(model, endpoint, key))


# ── Slash commands ──

COMMANDS = {}


def cmd(name, help_text=""):
    """Register a slash command."""
    def decorator(fn):
        COMMANDS[name] = {"fn": fn, "help": help_text}
        return fn
    return decorator


@cmd("help", "Show available commands")
async def _cmd_help(client, endpoint, headers, args):
    console.print()
    console.print("[bold]Slash commands:[/bold]")
    for name, info in sorted(COMMANDS.items()):
        console.print(f"  [green]/{name}[/green]  {info['help']}")
    console.print(f"  [green]/q[/green]      Exit")
    console.print()


@cmd("status", "Show node status")
async def _cmd_status(client, endpoint, headers, args):
    resp = await client.get(f"{endpoint}/v1/node/status", headers=headers)
    d = resp.json()
    console.print(f"\n[bold]{d.get('node_name', '?')}[/bold] ({d.get('mode', '?')})")
    console.print(f"  Peer ID:  [dim]{d.get('peer_id', '?')[:20]}...[/dim]")
    console.print(f"  Uptime:   {_fmt_uptime(d.get('uptime_seconds', 0))}")
    console.print(f"  Models:   {len(d.get('models', []))}")
    console.print(f"  Peers:    {len(d.get('peers', []))}")
    hw = d.get('hardware', {})
    console.print(f"  Hardware: {hw.get('gpu', 'CPU')} ({hw.get('vram_gb', 0)}GB, {hw.get('backend', 'cpu')})")
    console.print()


@cmd("models", "List loaded models")
async def _cmd_models(client, endpoint, headers, args):
    resp = await client.get(f"{endpoint}/v1/models", headers=headers)
    models = resp.json().get("data", [])
    if not models:
        console.print("\n[dim]No models loaded.[/dim]\n")
        return
    console.print(f"\n[bold]{len(models)} model(s):[/bold]")
    for m in models:
        owner = m.get("owned_by", "local")
        console.print(f"  [green]{m['id']}[/green] [dim]({owner})[/dim]")
    console.print()


@cmd("credits", "Show credit balance")
async def _cmd_credits(client, endpoint, headers, args):
    resp = await client.get(f"{endpoint}/v1/node/credits", headers=headers)
    d = resp.json()
    console.print(f"\n  Balance: [bold yellow]{d.get('balance', 0):.2f}[/bold yellow]")
    console.print(f"  Earned:  [green]+{d.get('earned', 0):.2f}[/green]")
    console.print(f"  Spent:   [red]-{d.get('spent', 0):.2f}[/red]\n")


@cmd("fleet", "Show fleet nodes")
async def _cmd_fleet(client, endpoint, headers, args):
    resp = await client.get(f"{endpoint}/v1/admin/nodes", headers=headers)
    nodes = resp.json().get("nodes", [])
    if not nodes:
        console.print("\n[dim]No fleet nodes registered.[/dim]\n")
        return
    console.print(f"\n[bold]{len(nodes)} fleet node(s):[/bold]")
    for n in nodes:
        status_color = "green" if n.get("online") else ("yellow" if n.get("status") == "pending" else "red")
        models = n.get("capabilities", {}).get("models", [])
        model_names = ", ".join(m.get("name", m) if isinstance(m, dict) else m for m in models[:3])
        console.print(
            f"  [{status_color}]{n.get('status', '?'):8s}[/{status_color}] "
            f"[bold]{n.get('node_name', '?')}[/bold] "
            f"[dim]{n.get('api_addr', '?')}[/dim]"
            f"{'  ' + model_names if model_names else ''}"
        )
    console.print()


@cmd("use", "Switch model: /use <model-name>")
async def _cmd_use(client, endpoint, headers, args):
    if not args:
        console.print("[dim]Usage: /use <model-name>[/dim]")
        return
    # Return the model name — handled by caller
    return args.strip()


@cmd("clear", "Clear conversation history")
async def _cmd_clear(client, endpoint, headers, args):
    return "__clear__"


@cmd("config", "Show node configuration")
async def _cmd_config(client, endpoint, headers, args):
    resp = await client.get(f"{endpoint}/v1/node/debug/config", headers=headers)
    d = resp.json()
    console.print()
    for k, v in d.items():
        console.print(f"  [dim]{k}:[/dim] {v}")
    console.print()


# ── Chat loop ──

async def _chat_loop(model: str, endpoint: str, api_key: str) -> None:
    import httpx
    from rich.markdown import Markdown
    from rich.live import Live
    from rich.text import Text
    from mycellm.cli.banner import print_banner, SPORE_GREEN, COMPUTE_RED, RELAY_BLUE, LEDGER_GOLD

    print_banner(console)

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Auto-detect model if not specified
    if not model:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                resp = await c.get(f"{endpoint}/v1/models", headers=headers)
                models = resp.json().get("data", [])
                if models:
                    model = models[0]["id"]
        except Exception:
            pass

    if model:
        console.print(f"  Model: [bold green]{model}[/bold green]")
    else:
        console.print(f"  Model: [yellow]auto[/yellow] (will use best available)")
    console.print(f"  Node:  [dim]{endpoint}[/dim]")
    console.print(f"  Type [green]/help[/green] for commands, [green]/q[/green] to exit\n")

    messages: list[dict] = []
    current_model = model

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0)) as client:
        while True:
            try:
                user_input = console.input(f"[bold {SPORE_GREEN}]> [/bold {SPORE_GREEN}]")
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Goodbye.[/dim]")
                break

            stripped = user_input.strip()
            if not stripped:
                continue

            if stripped.lower() in ("exit", "quit", "/q"):
                console.print("[dim]Goodbye.[/dim]")
                break

            # Slash command dispatch
            if stripped.startswith("/"):
                parts = stripped[1:].split(None, 1)
                cmd_name = parts[0].lower()
                cmd_args = parts[1] if len(parts) > 1 else ""

                if cmd_name in COMMANDS:
                    try:
                        result = await COMMANDS[cmd_name]["fn"](client, endpoint, headers, cmd_args)
                        if cmd_name == "use" and result:
                            current_model = result
                            console.print(f"  Switched to [bold green]{current_model}[/bold green]\n")
                        elif result == "__clear__":
                            messages.clear()
                            console.print("  [dim]Conversation cleared.[/dim]\n")
                    except httpx.ConnectError:
                        console.print(f"[red]Cannot connect to {endpoint}[/red]. Is the daemon running?")
                    except Exception as e:
                        console.print(f"[red]Command error: {e}[/red]")
                else:
                    console.print(f"[dim]Unknown command: /{cmd_name}. Type /help for available commands.[/dim]")
                continue

            # Chat message
            messages.append({"role": "user", "content": stripped})

            try:
                full_text = ""
                resp_model = ""

                async with client.stream(
                    "POST",
                    f"{endpoint}/v1/chat/completions",
                    json={
                        "model": current_model or "auto",
                        "messages": messages,
                        "stream": True,
                    },
                    headers={**headers, "Content-Type": "application/json"},
                ) as resp:
                    if resp.status_code == 401:
                        console.print("[red]Unauthorized.[/red] Set --api-key or MYCELLM_API_KEY.")
                        messages.pop()
                        continue
                    if resp.status_code != 200:
                        body = await resp.aread()
                        console.print(f"[red]Error {resp.status_code}:[/red] {body.decode()[:200]}")
                        messages.pop()
                        continue

                    console.print()
                    with Live(Text("", style="dim"), console=console, refresh_per_second=12, transient=True) as live:
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            payload = line[6:].strip()
                            if payload == "[DONE]":
                                break

                            import json
                            try:
                                chunk = json.loads(payload)
                                resp_model = chunk.get("model", resp_model)
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    full_text += content
                                    live.update(Markdown(full_text))
                            except Exception:
                                pass

                    # Final render (non-transient)
                    if full_text:
                        console.print(Markdown(full_text))

                messages.append({"role": "assistant", "content": full_text})

                # Attribution line
                via = resp_model or current_model or "auto"
                console.print(f"[dim]  via {via}[/dim]\n")

            except httpx.ConnectError:
                console.print(f"\n[red]Cannot connect to {endpoint}[/red]. Is 'mycellm serve' running?\n")
                messages.pop()
            except Exception as e:
                console.print(f"\n[red]Error: {e}[/red]\n")
                messages.pop()


def _fmt_uptime(seconds: float) -> str:
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    m = int((seconds % 3600) // 60)
    if d > 0:
        return f"{d}d {h}h {m}m"
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"
