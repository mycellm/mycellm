"""macOS menu bar monitor for a local mycellm node.

A glanceable mushroom in the menu bar: brand-green when the node is healthy,
cycling through the Protocol Palette while inference is in flight, gold when
reachable but serving nothing, gray when the node is offline. Management
stays in the local web dashboard — the dropdown links to it.

UI lives in `mycellm.menubar.app` (requires the `menubar` extra: rumps).
State/polling logic lives in `mycellm.menubar.state` and is platform-neutral.
"""
