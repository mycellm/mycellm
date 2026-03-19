"""ASCII mushroom banner and ANSI-colored log formatting."""

from rich.console import Console
from rich.text import Text

# ASCII mushroom — displayed in Spore Green on startup
ASCII_MUSHROOM = r"""
    ████████████████████
   ██████████████████████
  ████████████████████████
  ██████  ████████  ██████
  ██████  ████████  ██████
  ████████████████████████
   ██████████████████████
    ████████████████████
       ██████████████
        ████████████
         ██████████
        ████████████
       ██████████████
"""

# Brand colors
SPORE_GREEN = "#22C55E"
COMPUTE_RED = "#EF4444"
RELAY_BLUE = "#3B82F6"
LEDGER_GOLD = "#FACC15"
POISON_PURPLE = "#A855F7"
VOID_BLACK = "#0A0A0A"
CONSOLE_GRAY = "#E5E5E5"

# Log tag → color mapping
TAG_COLORS = {
    "VRAM": COMPUTE_RED,
    "INFER": COMPUTE_RED,
    "P2P": RELAY_BLUE,
    "DHT": RELAY_BLUE,
    "ROUTING": RELAY_BLUE,
    "CREDIT": LEDGER_GOLD,
    "SPORES": POISON_PURPLE,
    "SECURITY": POISON_PURPLE,
    "NODE": SPORE_GREEN,
    "BOOT": SPORE_GREEN,
    "API": SPORE_GREEN,
    "ROOTS": CONSOLE_GRAY,
}


def print_banner(console: Console | None = None) -> None:
    """Print the ASCII mushroom banner in Spore Green."""
    c = console or Console()
    text = Text(ASCII_MUSHROOM)
    text.stylize(f"bold {SPORE_GREEN}")
    c.print(text)
    c.print(
        Text("  mycellm.", style=f"bold {SPORE_GREEN}"),
        Text("  Distributed LLM inference.", style="dim"),
    )
    c.print()


def styled_tag(tag: str) -> str:
    """Return a Rich-formatted tag string with protocol colors."""
    color = TAG_COLORS.get(tag, CONSOLE_GRAY)
    return f"[{color}][{tag}][/{color}]"
