"""CLI command: mycellm serve — start the daemon."""

from __future__ import annotations

import typer
from rich.console import Console

from mycellm.cli.banner import print_banner

console = Console()
app = typer.Typer(invoke_without_command=True)

PRIORITY_NICE = {"low": 15, "normal": 0, "high": -5}


@app.callback(invoke_without_command=True)
def serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="API bind address"),
    port: int = typer.Option(8420, "--port", "-p", help="API port"),
    quic_port: int = typer.Option(8421, "--quic-port", help="QUIC transport port"),
    dht_port: int = typer.Option(8422, "--dht-port", help="DHT discovery port"),
    device: str = typer.Option("default", "--device", "-d", help="Device certificate name"),
    no_dht: bool = typer.Option(False, "--no-dht", help="Disable DHT discovery"),
    priority: str = typer.Option("normal", "--priority", help="Process priority: low, normal, high"),
    watchdog: bool = typer.Option(False, "--watchdog", help="Auto-restart on crash"),
    install_service: bool = typer.Option(False, "--install-service", help="Install as system service (launchd/systemd)"),
    uninstall_service: bool = typer.Option(False, "--uninstall-service", help="Remove system service"),
) -> None:
    """Start the mycellm node daemon."""
    import asyncio
    import os

    if install_service:
        _install_service(host, port, quic_port, dht_port, device, no_dht, priority)
        return

    if uninstall_service:
        _uninstall_service()
        return

    # Set process priority
    nice_val = PRIORITY_NICE.get(priority, 0)
    if nice_val != 0:
        try:
            os.nice(nice_val)
        except PermissionError:
            if nice_val < 0:
                console.print(f"[yellow]Cannot set high priority (needs root). Running at normal.[/yellow]")
            else:
                os.nice(nice_val)  # low priority should always work

    print_banner(console)

    priority_label = priority if priority in PRIORITY_NICE else "normal"
    console.print(f"[dim]Starting daemon on {host}:{port} (priority={priority_label})...[/dim]")

    from mycellm.node import MycellmNode

    node = MycellmNode(
        api_host=host,
        api_port=port,
        quic_port=quic_port,
        dht_port=dht_port,
        device_name=device,
        enable_dht=not no_dht,
    )

    if watchdog:
        _run_with_watchdog(node, console)
    else:
        try:
            asyncio.run(node.run())
        except KeyboardInterrupt:
            console.print("\n[dim]Shutting down...[/dim]")


def _run_with_watchdog(node, console) -> None:
    """Run the node with auto-restart on crash."""
    import asyncio
    import time

    max_restarts = 10
    restart_window = 300  # seconds
    restart_times: list[float] = []

    while True:
        try:
            console.print("[dim]Watchdog: starting node...[/dim]")
            asyncio.run(node.run())
            break  # clean exit
        except KeyboardInterrupt:
            console.print("\n[dim]Shutting down (watchdog exit)...[/dim]")
            break
        except SystemExit:
            break
        except Exception as e:
            now = time.time()
            restart_times = [t for t in restart_times if now - t < restart_window]
            restart_times.append(now)

            if len(restart_times) >= max_restarts:
                console.print(f"[red]Watchdog: {max_restarts} crashes in {restart_window}s — giving up.[/red]")
                raise

            delay = min(2 ** len(restart_times), 30)
            console.print(f"[yellow]Watchdog: crashed ({e}), restarting in {delay}s... ({len(restart_times)}/{max_restarts})[/yellow]")
            time.sleep(delay)

            # Re-create node for fresh state
            from mycellm.node import MycellmNode
            node = MycellmNode(
                api_host=node.api_host,
                api_port=node.api_port,
                quic_port=node.quic_port,
                dht_port=node.dht_port,
                device_name=node.device_name,
                enable_dht=node.enable_dht,
            )


def _install_service(host, port, quic_port, dht_port, device, no_dht, priority):
    """Install mycellm as a system service."""
    import platform
    import shutil
    import sys
    from pathlib import Path

    mycellm_bin = shutil.which("mycellm") or sys.executable

    if platform.system() == "Darwin":
        _install_launchd(mycellm_bin, host, port, quic_port, dht_port, device, no_dht, priority)
    elif platform.system() == "Linux":
        _install_systemd(mycellm_bin, host, port, quic_port, dht_port, device, no_dht, priority)
    else:
        console.print(f"[red]Unsupported platform: {platform.system()}[/red]")


def _install_launchd(mycellm_bin, host, port, quic_port, dht_port, device, no_dht, priority):
    """Install macOS launchd plist for auto-start + auto-restart."""
    import os
    from pathlib import Path

    nice_val = PRIORITY_NICE.get(priority, 0)
    label = "com.mycellm.node"
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / f"{label}.plist"

    args = [mycellm_bin, "serve", "--host", host, "--port", str(port),
            "--quic-port", str(quic_port), "--dht-port", str(dht_port),
            "--device", device, "--priority", priority]
    if no_dht:
        args.append("--no-dht")

    args_xml = "\n".join(f"        <string>{a}</string>" for a in args)
    env_entries = ""
    bootstrap = os.environ.get("MYCELLM_BOOTSTRAP_PEERS", "")
    if bootstrap:
        env_entries = f"""
    <key>EnvironmentVariables</key>
    <dict>
        <key>MYCELLM_BOOTSTRAP_PEERS</key>
        <string>{bootstrap}</string>
    </dict>"""

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>{env_entries}
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>Nice</key>
    <integer>{nice_val}</integer>
    <key>StandardOutPath</key>
    <string>/tmp/mycellm.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/mycellm.log</string>
    <key>ProcessType</key>
    <string>{"Background" if nice_val > 0 else "Standard"}</string>
</dict>
</plist>
"""
    plist_path.write_text(plist)
    console.print(f"[green]Installed:[/green] {plist_path}")
    console.print(f"[dim]Priority: {priority} (nice {nice_val})[/dim]")

    # Load it
    import subprocess
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True)
    if result.returncode == 0:
        console.print(f"[green]Service loaded. mycellm will auto-start and auto-restart.[/green]")
    else:
        console.print(f"[yellow]Wrote plist but launchctl load failed. Run manually:[/yellow]")
        console.print(f"  launchctl load {plist_path}")


def _install_systemd(mycellm_bin, host, port, quic_port, dht_port, device, no_dht, priority):
    """Install Linux systemd user service."""
    import os
    from pathlib import Path

    nice_val = PRIORITY_NICE.get(priority, 0)
    service_dir = Path.home() / ".config" / "systemd" / "user"
    service_dir.mkdir(parents=True, exist_ok=True)
    service_path = service_dir / "mycellm.service"

    args = f"{mycellm_bin} serve --host {host} --port {port} --quic-port {quic_port} --dht-port {dht_port} --device {device} --priority {priority}"
    if no_dht:
        args += " --no-dht"

    env_lines = ""
    bootstrap = os.environ.get("MYCELLM_BOOTSTRAP_PEERS", "")
    if bootstrap:
        env_lines = f"Environment=MYCELLM_BOOTSTRAP_PEERS={bootstrap}"

    unit = f"""[Unit]
Description=mycellm distributed LLM inference node
After=network.target

[Service]
ExecStart={args}
Restart=on-failure
RestartSec=10
Nice={nice_val}
{env_lines}

[Install]
WantedBy=default.target
"""
    service_path.write_text(unit)
    console.print(f"[green]Installed:[/green] {service_path}")
    console.print(f"[dim]Priority: {priority} (nice {nice_val})[/dim]")

    import subprocess
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    result = subprocess.run(["systemctl", "--user", "enable", "--now", "mycellm.service"], capture_output=True)
    if result.returncode == 0:
        console.print("[green]Service enabled. mycellm will auto-start and auto-restart.[/green]")
    else:
        console.print("[yellow]Wrote unit file. Enable manually:[/yellow]")
        console.print("  systemctl --user enable --now mycellm.service")


def _uninstall_service():
    """Remove mycellm system service."""
    import platform
    from pathlib import Path

    import subprocess

    if platform.system() == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "com.mycellm.node.plist"
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
            plist.unlink()
            console.print(f"[green]Removed {plist}[/green]")
        else:
            console.print("[dim]No launchd service found.[/dim]")
    elif platform.system() == "Linux":
        unit = Path.home() / ".config" / "systemd" / "user" / "mycellm.service"
        if unit.exists():
            subprocess.run(["systemctl", "--user", "disable", "--now", "mycellm.service"], capture_output=True)
            unit.unlink()
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            console.print(f"[green]Removed {unit}[/green]")
        else:
            console.print("[dim]No systemd service found.[/dim]")
