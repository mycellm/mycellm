"""Terminal mushroom banner with brand colors and styled log formatting.

The mushroom is rendered using Unicode half-block characters (▀▄█) with
Rich true-color styling to match the SVG logo as closely as possible
in a terminal. Colors match the brand palette.
"""

from rich.console import Console
from rich.text import Text

# Brand colors
SPORE_GREEN = "#22C55E"
COMPUTE_RED = "#EF4444"
RELAY_BLUE = "#3B82F6"
LEDGER_GOLD = "#FACC15"
POISON_PURPLE = "#A855F7"
VOID_BLACK = "#0A0A0A"
CONSOLE_GRAY = "#E5E5E5"

# Logo palette
_R = COMPUTE_RED   # red cap
_W = "#FFFFFF"      # white spots
_G = "#FDE68A"      # gold/cream stem
_D = "#111827"      # dark (pupils)
_X = None           # transparent (background)

# Pixel grid derived from the SVG (10 columns x 14 rows)
# Mapped from the rect positions in the 1024x1024 SVG
_LOGO_GRID = [
    # Cap
    [_X, _X, _X, _R, _R, _R, _R, _X, _X, _X],  # row 0: cap crown
    [_X, _X, _R, _R, _R, _R, _R, _R, _X, _X],  # row 1: cap wide
    [_X, _R, _W, _W, _R, _R, _W, _W, _R, _X],  # row 2: eyes open
    [_R, _R, _W, _W, _R, _R, _W, _W, _R, _R],  # row 3: eyes
    [_R, _R, _W, _W, _R, _R, _W, _W, _R, _R],  # row 4: eyes bottom
    [_R, _R, _R, _R, _W, _W, _W, _R, _R, _R],  # row 5: mouth
    [_R, _R, _R, _R, _W, _W, _W, _R, _R, _R],  # row 6: mouth bottom
    # Stem
    [_X, _X, _G, _G, _G, _G, _G, _G, _X, _X],  # row 7: stem narrow
    [_X, _G, _G, _G, _G, _G, _G, _G, _G, _X],  # row 8: stem wide
    [_X, _G, _D, _G, _G, _G, _G, _D, _G, _X],  # row 9: windows top
    [_X, _G, _D, _G, _G, _G, _G, _D, _G, _X],  # row 10: windows bottom
    [_X, _G, _G, _G, _G, _G, _G, _G, _G, _X],  # row 11: stem close
    [_X, _X, _G, _G, _G, _G, _G, _G, _X, _X],  # row 12: stem base
    [_X, _X, _X, _X, _X, _X, _X, _X, _X, _X],  # row 13: padding (even rows)
]

# Half-block characters for 2:1 vertical resolution
_UPPER = "▀"
_LOWER = "▄"
_FULL = "█"


def _render_logo(width: int = 2) -> Text:
    """Render the mushroom logo using colored half-blocks.

    Each grid cell becomes `width` characters wide.
    Two grid rows are combined vertically using ▀▄ characters.
    """
    text = Text()
    rows = _LOGO_GRID
    # Pad to even row count
    if len(rows) % 2:
        rows = rows + [[_X] * len(rows[0])]

    for y in range(0, len(rows), 2):
        top_row = rows[y]
        bot_row = rows[y + 1]
        line = Text()
        for x in range(len(top_row)):
            tc = top_row[x]
            bc = bot_row[x]
            char = _FULL * width
            if tc and bc:
                if tc == bc:
                    line.append(char, style=f"bold {tc}")
                else:
                    line.append(_UPPER * width, style=f"bold {tc} on {bc}")
            elif tc and not bc:
                line.append(_UPPER * width, style=f"bold {tc}")
            elif not tc and bc:
                line.append(_LOWER * width, style=f"bold {bc}")
            else:
                line.append(" " * width)
        text.append("    ")  # indent
        text.append(line)
        text.append("\n")
    return text


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
    """Print the branded mushroom logo and tagline."""
    c = console or Console()
    c.print()
    c.print(_render_logo(width=2))
    c.print(
        Text("  mycellm_", style=f"bold {COMPUTE_RED}"),
        Text("  Distributed LLM inference.", style="dim"),
    )
    c.print()


def print_chat_header(console: Console | None = None) -> None:
    """Print a compact branded header for the chat REPL."""
    c = console or Console()
    c.print()
    c.print(_render_logo(width=2))
    c.print(
        Text("  mycellm_", style=f"bold {COMPUTE_RED}"),
        Text(" chat", style=f"bold {SPORE_GREEN}"),
    )
    c.print(f"  [{CONSOLE_GRAY}]{'─' * 40}[/{CONSOLE_GRAY}]")


def styled_tag(tag: str) -> str:
    """Return a Rich-formatted tag string with protocol colors."""
    color = TAG_COLORS.get(tag, CONSOLE_GRAY)
    return f"[{color}][{tag}][/{color}]"


def prompt_style() -> str:
    """Return the Rich style string for the chat prompt."""
    return f"bold {SPORE_GREEN}"
