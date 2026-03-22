"""CLI command: mycellm chat — interactive chat REPL with slash commands.

An interactive terminal for chatting with models on the mycellm network
and managing your node — inspired by Claude Code.

Features:
  - Streaming responses with Rich markdown rendering
  - Slash commands for node/fleet/model management
  - Auto-selects best available model (or specify with --model)
  - Multi-turn conversation context
  - Branded mushroom banner + green-bordered input
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
    private: bool = typer.Option(False, "--private", "-p", help="Route only to local/trusted nodes (for sensitive data)"),
) -> None:
    """Interactive chat REPL with streaming and slash commands."""
    import asyncio
    import os

    key = api_key or os.environ.get("MYCELLM_API_KEY", "")
    trust = "local" if private else ""
    asyncio.run(_chat_loop(model, endpoint, key, trust))


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
            f"  [{status_color}]●[/{status_color}] "
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
    return args.strip()


@cmd("clear", "Clear conversation history")
async def _cmd_clear(client, endpoint, headers, args):
    return "__clear__"


@cmd("relay", "Manage relays: /relay [add|remove|refresh] [url]")
async def _cmd_relay(client, endpoint, headers, args):
    parts = args.strip().split(None, 1) if args else []
    action = parts[0].lower() if parts else ""
    url = parts[1].strip() if len(parts) > 1 else ""

    if action == "add" and url:
        resp = await client.post(
            f"{endpoint}/v1/node/relay/add",
            json={"url": url},
            headers=headers,
        )
        d = resp.json()
        if d.get("error"):
            console.print(f"  [red]{d['error']}[/red]")
        else:
            r = d.get("relay", {})
            status = "[green]online[/green]" if r.get("online") else f"[red]offline[/red] ({r.get('error', '')})"
            console.print(f"\n  {status} — {r.get('name', url)}")
            for m in r.get("models", []):
                console.print(f"    [green]relay:{m}[/green]")
            console.print()
        return

    if action == "remove" and url:
        resp = await client.post(
            f"{endpoint}/v1/node/relay/remove",
            json={"url": url},
            headers=headers,
        )
        d = resp.json()
        console.print(f"  [dim]{d.get('status', 'error')}[/dim]\n")
        return

    if action == "refresh":
        resp = await client.post(
            f"{endpoint}/v1/node/relay/refresh", json={}, headers=headers,
        )
        d = resp.json()
        console.print(f"\n  Discovered {d.get('models_discovered', 0)} new model(s)\n")
        return

    # Default: list relays
    resp = await client.get(f"{endpoint}/v1/node/relay", headers=headers)
    relays = resp.json().get("relays", [])
    if not relays:
        console.print("\n  [dim]No relay backends configured.[/dim]")
        console.print("  [dim]Add one: /relay add http://device.lan:8080[/dim]\n")
        return
    console.print(f"\n[bold]{len(relays)} relay(s):[/bold]")
    for r in relays:
        status = "[green]●[/green]" if r["online"] else "[red]●[/red]"
        console.print(f"  {status} [bold]{r['name']}[/bold] [dim]{r['url']}[/dim]")
        for m in r.get("models", []):
            console.print(f"      [green]relay:{m}[/green]")
    console.print()


@cmd("config", "Show node configuration")
async def _cmd_config(client, endpoint, headers, args):
    resp = await client.get(f"{endpoint}/v1/node/debug/config", headers=headers)
    d = resp.json()
    console.print()
    for k, v in d.items():
        console.print(f"  [dim]{k}:[/dim] {v}")
    console.print()


# Public bootstrap — zero-config fallback
PUBLIC_BOOTSTRAP = "https://api.mycellm.dev"


async def _discover_endpoint(endpoint: str, headers: dict, console: Console) -> tuple[str, str]:
    """Auto-discover the best endpoint and model.

    Discovery chain:
      1. Local node (localhost:8420) — check for loaded + fleet + peer models
      2. Configured bootstrap (from MYCELLM_BOOTSTRAP_PEERS .env) — LAN/private network
      3. Public bootstrap (api.mycellm.dev) — zero-config last resort

    Returns (endpoint, model_name). Model may be empty if nothing found.
    """
    import httpx
    from mycellm.cli.banner import SPORE_GREEN, LEDGER_GOLD

    async def _try_endpoint(url: str, client: httpx.AsyncClient) -> list[dict]:
        try:
            resp = await client.get(f"{url}/v1/models", headers=headers)
            if resp.status_code == 200:
                return resp.json().get("data", [])
        except Exception:
            pass
        return []

    async with httpx.AsyncClient(timeout=5) as c:
        # 1. Local node
        models = await _try_endpoint(endpoint, c)
        if models:
            return endpoint, models[0]["id"]

        # 2. Configured bootstrap (LAN / private network)
        try:
            local_running = True
            from mycellm.config import get_settings
            settings = get_settings()
            bootstrap = settings.get_bootstrap_list()
            if bootstrap:
                bhost, bport = bootstrap[0]
                bapi = bport - 1
                bootstrap_url = f"http://{bhost}:{bapi}"
                models = await _try_endpoint(bootstrap_url, c)
                if models:
                    console.print(f"  [{LEDGER_GOLD}]No local models — connecting to {bootstrap_url}[/{LEDGER_GOLD}]")
                    return bootstrap_url, models[0]["id"]
        except Exception:
            local_running = False

        # 3. Public network (zero-config fallback)
        try:
            resp = await c.get(f"{PUBLIC_BOOTSTRAP}/v1/node/public/stats")
            if resp.status_code == 200:
                stats = resp.json()
                model_names = stats.get("models", {}).get("names", [])
                if model_names:
                    console.print(f"  [{LEDGER_GOLD}]Using public mycellm network ({stats.get('nodes', {}).get('online', '?')} nodes online)[/{LEDGER_GOLD}]")
                    return PUBLIC_BOOTSTRAP, ""  # gateway auto-selects
        except Exception:
            pass

        # Nothing found — give helpful guidance
        if not local_running:
            console.print(f"  [red]No mycellm node running.[/red]")
            console.print(f"  [dim]Start one:  mycellm init && mycellm serve[/dim]")
        else:
            console.print(f"  [yellow]No models available on the network.[/yellow]")
            console.print(f"  [dim]Load one:   open http://localhost:8420 → Models tab[/dim]")

    return endpoint, ""


# ── Chat loop ──

async def _chat_loop(model: str, endpoint: str, api_key: str, trust: str = "") -> None:
    import httpx
    from rich.markdown import Markdown
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    from mycellm.cli.banner import print_chat_header, SPORE_GREEN, COMPUTE_RED, CONSOLE_GRAY

    print_chat_header(console)

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Auto-discover: local → configured bootstrap → public network
    if not model:
        endpoint, model = await _discover_endpoint(endpoint, headers, console)

    if model:
        console.print(f"  Model: [bold green]{model}[/bold green]")
    else:
        console.print(f"  Model: [yellow]auto[/yellow] (routes to best available on network)")
    console.print(f"  Node:  [dim]{endpoint}[/dim]")
    if trust:
        trust_labels = {"local": "local only (no network)", "trusted": "trusted peers only"}
        console.print(f"  Trust: [bold yellow]{trust_labels.get(trust, trust)}[/bold yellow]")
    console.print(f"  Type [green]/help[/green] for commands, [green]/q[/green] to exit")
    console.print(f"  [dim]Tip: run 'mycellm init' to configure your node[/dim]\n")

    messages: list[dict] = []
    current_model = model

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=300.0)) as client:
        while True:
            # Green-bordered input prompt
            try:
                user_input = console.input(
                    f"[{SPORE_GREEN}]╭──[/{SPORE_GREEN}]\n"
                    f"[{SPORE_GREEN}]│[/{SPORE_GREEN}] "
                )
            except (EOFError, KeyboardInterrupt):
                console.print(f"\n[dim]Goodbye.[/dim]")
                break

            # Close the input border
            console.print(f"[{SPORE_GREEN}]╰──[/{SPORE_GREEN}]")

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

            # Sensitive content scan
            from mycellm.privacy import scan_sensitive
            matches = scan_sensitive(stripped)
            if matches:
                from mycellm.cli.banner import COMPUTE_RED, LEDGER_GOLD
                for m in matches:
                    icon = f"[{COMPUTE_RED}]!!![/{COMPUTE_RED}]" if m.severity == "high" else f"[{LEDGER_GOLD}]![/{LEDGER_GOLD}]"
                    console.print(f"  {icon} {m.label}: [dim]{m.pattern}[/dim]")
                console.print(f"  [dim]Prompts are processed by distributed nodes.[/dim]")
                try:
                    if not typer.confirm("  Send anyway?", default=False):
                        continue
                except (EOFError, KeyboardInterrupt):
                    continue

            # Chat message
            messages.append({"role": "user", "content": stripped})

            try:
                import json as json_mod
                import time as time_mod

                full_text = ""
                resp_model = ""
                node_id = ""
                server_latency = 0
                start_time = time_mod.time()

                # Use public gateway for public bootstrap, authenticated endpoint otherwise
                chat_path = "/v1/public/chat/completions" if endpoint.startswith("https://") else "/v1/chat/completions"
                async with client.stream(
                    "POST",
                    f"{endpoint}{chat_path}",
                    json={
                        "model": current_model or "auto",
                        "messages": messages,
                        "stream": True,
                        **({"mycellm": {"trust": trust}} if trust else {}),
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
                    with Live(Text("  ● ● ●", style=f"bold {SPORE_GREEN}"), console=console, refresh_per_second=10, transient=True) as live:
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            payload = line[6:].strip()
                            if payload == "[DONE]":
                                break

                            try:
                                chunk = json_mod.loads(payload)
                                resp_model = chunk.get("model", resp_model)
                                # Extract node attribution
                                meta = chunk.get("mycellm", {})
                                if meta:
                                    node_id = meta.get("node", node_id)
                                    if meta.get("latency_ms"):
                                        server_latency = meta["latency_ms"]
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

                # Attribution line — model, node, latency (like portal + dashboard)
                latency = server_latency or round((time_mod.time() - start_time) * 1000)
                via = resp_model or current_model or "auto"
                parts = [f"[dim]{via}[/dim]"]
                if node_id:
                    parts.append(f"[dim]via node[/dim] [{SPORE_GREEN}]{node_id}[/{SPORE_GREEN}]")
                parts.append(f"[dim]{latency}ms[/dim]")
                console.print(f"\n  {' · '.join(parts)}\n")

            except httpx.ConnectError:
                console.print(f"\n[red]Cannot connect to {endpoint}[/red]. Is 'mycellm serve' running?\n")
                messages.pop()
            except KeyboardInterrupt:
                console.print(f"\n[dim]Interrupted.[/dim]\n")
                if messages and messages[-1]["role"] == "user":
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
