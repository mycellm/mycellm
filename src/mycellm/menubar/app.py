"""The rumps (AppKit) menu bar UI. macOS only; import lazily.

Icon language (brand "Protocol States"):
- Spore Green   — node healthy, models loaded, idle
- palette cycle — inference in flight (red → blue → gold → purple → green)
- Ledger Gold   — reachable but nothing loaded
- gray          — node offline

Monochrome Icon (toggle in the menu, persisted to ~/.config/mycellm/
menubar.json) swaps in a black-and-alpha template mushroom that macOS
tints to match the menu bar; state is carried by opacity instead of color
(full = healthy, soft = empty/pulse, dim = offline).

Dropdown: status, a 10-minute tok/s + activity time graph, model details,
peer ID (click to reveal, click again to copy), version/uptime/hardware.
Management stays in the local web dashboard.
"""

from __future__ import annotations

import webbrowser

import rumps
from AppKit import NSPasteboard, NSPasteboardTypeString

from . import launchagent
from .graph import render_graph
from .state import (
    History,
    NodeSnapshot,
    credits_line,
    fetch_snapshot,
    graph_caption,
    hardware_line,
    icon_is_template,
    icon_path,
    icon_variant,
    load_prefs,
    model_detail_lines,
    models_line,
    peer_id_line,
    peers_line,
    save_prefs,
    status_line,
    uptime_line,
)

POLL_SECONDS = 5
ANIMATE_SECONDS = 0.6


class MenubarApp(rumps.App):
    def __init__(self, api: str) -> None:
        self.api = api.rstrip("/")
        # Node API key (config .env / env var) — without it a key-protected
        # node 401s every poll and the monitor reports a healthy node offline.
        try:
            from mycellm.config import get_settings
            self._api_key = get_settings().api_key or ""
        except Exception:
            self._api_key = ""
        self._prefs = load_prefs()
        self._mono = bool(self._prefs.get("monochrome", False))
        first = "mono-dim" if self._mono else "gray"
        super().__init__(
            "mycellm",
            icon=icon_path(first),
            template=icon_is_template(first),
            quit_button=None,
        )
        self._snap = NodeSnapshot()
        self._history = History()
        self._tick = 0
        self._variant = first
        self._peer_revealed = False
        self._peer_flash = False

        self._status_item = rumps.MenuItem(status_line(self._snap))
        self._graph_item = rumps.MenuItem("")
        self._caption_item = rumps.MenuItem(graph_caption(self._history, self._snap))
        self._models_item = rumps.MenuItem(models_line(self._snap))
        self._peers_item = rumps.MenuItem(peers_line(self._snap))
        self._credits_item = rumps.MenuItem(credits_line(self._snap))
        self._peer_id_item = rumps.MenuItem(
            peer_id_line(self._snap), callback=self._peer_id_clicked
        )
        self._uptime_item = rumps.MenuItem(uptime_line(self._snap))
        self._hardware_item = rumps.MenuItem(hardware_line(self._snap))
        self._login_item = rumps.MenuItem(
            "Launch at Login", callback=self._toggle_login
        )
        self._login_item.state = 1 if launchagent.is_installed() else 0
        self._mono_item = rumps.MenuItem(
            "Monochrome Icon", callback=self._toggle_mono
        )
        self._mono_item.state = 1 if self._mono else 0

        self.menu = [
            self._status_item,
            None,
            self._graph_item,
            self._caption_item,
            None,
            self._models_item,
            self._peers_item,
            self._credits_item,
            None,
            self._peer_id_item,
            self._uptime_item,
            self._hardware_item,
            None,
            rumps.MenuItem("Open Dashboard…", callback=self._open_dashboard),
            None,
            self._login_item,
            self._mono_item,
            rumps.MenuItem("Hide Icon (until next launch)", callback=self._hide),
            rumps.MenuItem("Quit mycellm Monitor", callback=self._quit),
        ]

        rumps.Timer(self._poll, POLL_SECONDS).start()
        rumps.Timer(self._animate, ANIMATE_SECONDS).start()
        self._poll(None)

    # ---- timers ---------------------------------------------------------

    def _poll(self, _timer) -> None:
        self._snap = fetch_snapshot(self.api, api_key=self._api_key)
        self._history.append(self._snap)

        self._status_item.title = status_line(self._snap)
        self._caption_item.title = graph_caption(self._history, self._snap)
        self._models_item.title = models_line(self._snap)
        self._refresh_model_submenu()
        self._peers_item.title = peers_line(self._snap)
        self._credits_item.title = credits_line(self._snap)
        self._uptime_item.title = uptime_line(self._snap)
        self._hardware_item.title = hardware_line(self._snap)
        self._graph_item._menuitem.setImage_(render_graph(self._history))

        if self._peer_flash:
            self._peer_flash = False
        else:
            self._peer_id_item.title = peer_id_line(self._snap, self._peer_revealed)
        self._apply_icon()

    def _refresh_model_submenu(self) -> None:
        lines = model_detail_lines(self._snap)
        # Rebuild the submenu only when content changed (avoids flicker).
        # clear() crashes on an item that never had children (rumps lazily
        # creates the backing NSMenu on first add), so only clear non-empty.
        try:
            current = [item.title for item in self._models_item.values()]
            if current == lines:
                return
            if current:
                self._models_item.clear()
            for line in lines:
                self._models_item.add(rumps.MenuItem(line))
        except Exception as exc:  # noqa: BLE001 — cosmetic submenu must not kill the app
            print(f"model submenu refresh failed: {exc}", flush=True)

    def _animate(self, _timer) -> None:
        if self._snap.active > 0:
            self._tick += 1
            self._apply_icon()

    def _apply_icon(self) -> None:
        variant = icon_variant(self._snap, self._tick, mono=self._mono)
        if variant != self._variant:
            self._variant = variant
            # rumps applies self.template when the icon is set, so keep it
            # in sync first (template icons are tinted by macOS).
            self._template = icon_is_template(variant)
            self.icon = icon_path(variant)

    # ---- actions --------------------------------------------------------

    def _peer_id_clicked(self, item) -> None:
        if not self._snap.peer_id:
            return
        if not self._peer_revealed:
            self._peer_revealed = True
            item.title = peer_id_line(self._snap, revealed=True)
            return
        pasteboard = NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(self._snap.peer_id, NSPasteboardTypeString)
        self._peer_revealed = False
        self._peer_flash = True  # keep the confirmation up until next poll
        item.title = "Peer ID: copied ✓"

    def _open_dashboard(self, _item) -> None:
        webbrowser.open(self.api)

    def _toggle_mono(self, item) -> None:
        self._mono = not self._mono
        item.state = 1 if self._mono else 0
        self._prefs["monochrome"] = self._mono
        save_prefs(self._prefs)
        self._variant = ""  # force re-apply even if the name matches
        self._apply_icon()

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
    # Accessory policy = menu bar extra only. Without this the process is a
    # regular app (the venv runs the framework Python.app bundle), so macOS
    # puts a "Python" tile in the Dock for as long as the monitor runs.
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory
    )
    MenubarApp(api).run()
