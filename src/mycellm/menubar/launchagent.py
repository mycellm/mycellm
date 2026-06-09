"""Launch-at-login via a per-user LaunchAgent (no SMAppService — we're not
a bundled .app, just a console script in the user's environment)."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.mycellm.menubar"


def agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def plist_path(directory: Path | None = None) -> Path:
    return (directory or agents_dir()) / f"{LABEL}.plist"


def _menubar_command(api: str) -> list[str]:
    """Resolve the command that relaunches this menubar app at login.

    Prefer the `mycellm` next to the interpreter that's installing the
    agent: machines with several mycellm installs (venv + homebrew + dev
    checkouts) must relaunch the one that actually has the menubar extra,
    not whichever happens to be first on PATH.
    """
    sibling = Path(sys.executable).parent / "mycellm"
    if sibling.exists():
        return [str(sibling), "menubar", "--api", api]
    return [shutil.which("mycellm") or "mycellm", "menubar", "--api", api]


def plist_content(api: str) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": _menubar_command(api),
        "RunAtLoad": True,
        "ProcessType": "Interactive",
        "LimitLoadToSessionType": "Aqua",
    }


def is_installed(directory: Path | None = None) -> bool:
    return plist_path(directory).exists()


def install(api: str, directory: Path | None = None) -> Path:
    path = plist_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        plistlib.dump(plist_content(api), fh)
    return path


def remove(directory: Path | None = None) -> bool:
    path = plist_path(directory)
    if not path.exists():
        return False
    # Best effort: also unload it from launchd so it doesn't linger.
    subprocess.run(
        ["launchctl", "bootout", f"gui/{_uid()}/{LABEL}"],
        capture_output=True, check=False,
    )
    path.unlink()
    return True


def _uid() -> int:
    import os

    return os.getuid()
