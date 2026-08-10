"""Hyphae CLI: plan and run distributed coding tasks against your fleet."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .config import EXAMPLE_CONFIG, DeviceRegistry, HyphaeConfig
from .executor import HyphaeEngine, TaskStatus
from .planner import Planner, render_dag
from .spec import Role
from .workspace import Workspace

app = typer.Typer(help="Hyphae — distributed coding-agent harness for Mycellm.")
console = Console()

CONFIG_FILE = "hyphae.toml"


def _load_config(workspace: Path) -> HyphaeConfig:
    path = workspace / CONFIG_FILE
    if path.is_file():
        return HyphaeConfig.from_toml(path)
    # Degraded solo mode: a local mycellm node plays all roles.
    console.print(
        f"[yellow]No {CONFIG_FILE} found — using local mycellm node "
        "(http://localhost:8420/v1, model=auto) for all roles.[/yellow]"
    )
    return HyphaeConfig.single_endpoint("http://localhost:8420/v1", "auto")


@app.command()
def init(workspace: Path = typer.Option(Path("."), "--workspace", "-w")) -> None:
    """Write an example hyphae.toml into the workspace."""
    path = workspace / CONFIG_FILE
    if path.exists():
        console.print(f"[red]{path} already exists; not overwriting.[/red]")
        raise typer.Exit(1)
    path.write_text(EXAMPLE_CONFIG)
    console.print(f"[green]Wrote {path}.[/green] Edit device endpoints, then: hyphae run \"...\"")


@app.command()
def devices(workspace: Path = typer.Option(Path("."), "--workspace", "-w")) -> None:
    """Check connectivity to every configured device."""
    config = _load_config(workspace)
    table = Table(title="Hyphae fleet")
    for col in ("device", "role", "endpoint", "model", "status"):
        table.add_column(col)

    async def probe() -> list[tuple[str, str]]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            results = []
            for d in config.devices:
                try:
                    resp = await client.get(f"{d.base_url.rstrip('/')}/models")
                    status = "[green]online[/green]" if resp.status_code == 200 \
                        else f"[yellow]HTTP {resp.status_code}[/yellow]"
                except httpx.HTTPError as exc:
                    status = f"[red]unreachable ({type(exc).__name__})[/red]"
                results.append((d.name, status))
            return results

    statuses = dict(asyncio.run(probe()))
    for d in config.devices:
        table.add_row(d.name, d.role.value, d.base_url, d.model, statuses[d.name])
    console.print(table)


@app.command()
def plan(
    request: str,
    workspace: Path = typer.Option(Path("."), "--workspace", "-w"),
) -> None:
    """Decompose a request into a task DAG without executing it."""
    config = _load_config(workspace)
    registry = DeviceRegistry.from_config(config)
    ws = Workspace(workspace)
    planner = Planner(registry.get(Role.ARCHITECT).client)
    dag = asyncio.run(planner.plan(request, ws))
    console.print(render_dag(dag), markup=False)


@app.command()
def run(
    request: str,
    workspace: Path = typer.Option(Path("."), "--workspace", "-w"),
) -> None:
    """Plan and execute a request end-to-end, writing results to the workspace."""
    config = _load_config(workspace)
    registry = DeviceRegistry.from_config(config)
    ws = Workspace(workspace)
    engine = HyphaeEngine(registry, ws, config=config.engine)

    report = asyncio.run(engine.run(request))

    table = Table(title="Run report")
    for col in ("task", "status", "device", "iters", "micro", "warm start", "tests", "files"):
        table.add_column(col)
    for result in report.results.values():
        if result.tests_passed is None:
            tests = "-"
        elif result.tests_passed:
            tests = "[green]pass[/green]"
        else:
            tests = "[red]fail[/red]"
        table.add_row(
            result.task_id,
            "[green]done[/green]" if result.status is TaskStatus.DONE
            else f"[red]failed[/red] ({result.failure_reason})",
            result.device,
            str(result.iterations),
            str(result.micro_iterations),
            "yes" if result.warm_start_used else "",
            tests,
            ", ".join(result.files) or "-",
        )
    console.print(table)
    for event in report.events:
        console.print(f"[dim]• {escape(event)}[/dim]")
    if report.success:
        console.print(f"[green]✓ {len(report.files_written)} file(s) written.[/green]")
    else:
        console.print("[red]✗ run did not complete; see failures above.[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
