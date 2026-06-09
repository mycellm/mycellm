"""The rumps (AppKit) menu bar UI. macOS only; import lazily.

Icon language (brand "Protocol States"):
- Spore Green   — node healthy, models loaded, idle
- palette cycle — inference in flight (red → blue → gold → purple → green)
- Ledger Gold   — reachable but nothing loaded
- gray          — node offline
"""

from __future__ import annotations

import webbrowser

import rumps

from . import launchagent
from .state import (
    NodeSnapshot,
    credits_line,
    fetch_snapshot,
    icon_path,
    icon_variant,
    models_line,
    peers_line,
    status_line,
)

POLL_SECONDS = 5
ANIMATE_SECONDS = 0.6


class MenubarApp(rumps.App):
    def __init__(self, api: str) -> None:
        self.api = api.rstrip("/")
        super().__init__(
            "mycellm", icon=icon_path("gray"), template=False, quit_button=None
        )
        self._snap = NodeSnapshot()
        self._tick = 0
        self._variant = "gray"

        self._status_item = rumps.MenuItem(status_line(self._snap))
        self._models_item = rumps.MenuItem(models_line(self._snap))
        self._peers_item = rumps.MenuItem(peers_line(self._snap))
        self._credits_item = rumps.MenuItem(credits_line(self._snap))
        self._login_item = rumps.MenuItem(
            "Launch at Login", callback=self._toggle_login
        )
        self._login_item.state = 1 if launchagent.is_installed() else 0

        self.menu = [
            self._status_item,
            self._models_item,
            self._peers_item,
            self._credits_item,
            None,
            rumps.MenuItem("Open Dashboard…", callback=self._open_dashboard),
            None,
            self._login_item,
            rumps.MenuItem("Hide Icon (until next launch)", callback=self._hide),
            rumps.MenuItem("Quit mycellm Monitor", callback=self._quit),
        ]

        rumps.Timer(self._poll, POLL_SECONDS).start()
        rumps.Timer(self._animate, ANIMATE_SECONDS).start()
        self._poll(None)

    # ---- timers ---------------------------------------------------------

    def _poll(self, _timer) -> None:
        self._snap = fetch_snapshot(self.api)
        self._status_item.title = status_line(self._snap)
        self._models_item.title = models_line(self._snap)
        self._peers_item.title = peers_line(self._snap)
        self._credits_item.title = credits_line(self._snap)
        self._apply_icon()

    def _animate(self, _timer) -> None:
        if self._snap.active > 0:
            self._tick += 1
            self._apply_icon()

    def _apply_icon(self) -> None:
        variant = icon_variant(self._snap, self._tick)
        if variant != self._variant:
            self._variant = variant
            self.icon = icon_path(variant)

    # ---- actions --------------------------------------------------------

    def _open_dashboard(self, _item) -> None:
        webbrowser.open(self.api)

    def _toggle_login(self, item) -> None:
        if launchagent.is_installed():
            launchagent.remove()
            item.state = 0
        else:
            launchagent.install(self.api)
            item.state = 1

    def _hide(self, _item) -> None:
        # Hiding never touches the node — this is just the monitor process.
        # Relaunch any time with `mycellm menubar` (or at next login if
        # Launch at Login is enabled).
        rumps.quit_application()

    def _quit(self, _item) -> None:
        rumps.quit_application()


def run(api: str = "http://localhost:8420") -> None:
    MenubarApp(api).run()
