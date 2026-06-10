#!/usr/bin/env python3
"""Render the menu bar mushroom icons from the brand SVGs.

The brand logo-icon SVGs (website/brand/logo-icon/svg) are crisp-edge rect
grids — an 8-bit mushroom. We parse the rects directly with a regex (no SVG
library needed) and rasterize with PIL: paint at the native 1024 viewBox,
then LANCZOS-downscale to 44px (22pt @2x, the standard menu bar size).

Color icons (mushroom-<color>.png) use the SVG's own palette. Monochrome
template icons (mushroom-mono*.png) re-ink the same geometry in black with
per-region alpha — macOS tints template images to match the menu bar, so
they sit naturally next to the system's own icons in light and dark mode:

- cap   -> solid black
- spots -> fully transparent (punched out, classic template-icon look)
- stem  -> ~59% alpha (the brand's cream reads as a lighter tone)
- eyes  -> solid black

Three mono variants encode node state by overall opacity: mono (healthy),
mono-soft (x0.68 — no models / activity pulse), mono-dim (x0.42 — offline).

Usage: python scripts/render_menubar_icons.py [--size 44] [--color]
(mono only by default; --color re-renders the five palette icons too)
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
SVG_DIR = REPO.parent / "website" / "brand" / "logo-icon" / "svg"
ICON_DIR = REPO / "src" / "mycellm" / "menubar" / "icons"
VIEWBOX = 1024

# Region roles keyed by the fill found in the green brand SVG.
ROLE_BY_FILL = {
    "#22c55e": "cap",
    "#fff": "spots",
    "#fde68a": "stem",
    "#111827": "eyes",
}

# Black ink with per-region alpha for the template (mono) icon.
MONO_ALPHA = {"cap": 255, "spots": 0, "stem": 150, "eyes": 255}
MONO_VARIANTS = {"mono": 1.0, "mono-soft": 0.68, "mono-dim": 0.42}

COLOR_SVGS = {
    "green": "mycellm-green-logo-sans.svg",
    "red": "mycellm-red-logo-sans.svg",
    "blue": "mycellm-blue-logo-sans.svg",
    "gold": "mycellm-gold-logo-sans.svg",
    "purple": "mycellm-purple-logo-sans.svg",
}


def parse_svg(path: Path) -> list[tuple[str, float, float, float, float]]:
    """Return [(fill, x, y, w, h)] in SVG paint order."""
    svg = path.read_text()
    fills = dict(re.findall(r"\.(st\d+)\s*\{\s*fill:\s*(#[0-9a-fA-F]{3,6})", svg))
    rects = []
    for m in re.finditer(
        r'<rect class="(st\d+)" x="([\d.]+)"(?: y="([\d.]+)")?'
        r' width="([\d.]+)" height="([\d.]+)"',
        svg,
    ):
        cls, x, y, w, h = m.groups()
        rects.append((fills[cls].lower(), float(x), float(y or 0), float(w), float(h)))
    return rects


def rasterize(rects, ink, size: int) -> Image.Image:
    """Paint rects at viewBox scale with replacement semantics (painter's
    order: later rects punch through earlier ones, which is how the spots
    become transparent holes), then downscale."""
    im = Image.new("RGBA", (VIEWBOX, VIEWBOX), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    for fill, x, y, w, h in rects:
        rgba = ink(fill)
        if rgba is None:
            continue
        draw.rectangle(
            (round(x), round(y), round(x + w) - 1, round(y + h) - 1), fill=rgba
        )
    return im.resize((size, size), Image.LANCZOS)


def hex_to_rgba(value: str) -> tuple[int, int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), 255)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=44, help="output px (22pt @2x)")
    ap.add_argument("--color", action="store_true", help="also re-render color icons")
    args = ap.parse_args()

    green_rects = parse_svg(SVG_DIR / COLOR_SVGS["green"])

    for name, scale in MONO_VARIANTS.items():
        def mono_ink(fill, scale=scale):
            role = ROLE_BY_FILL.get(fill)
            if role is None:
                return None
            alpha = round(MONO_ALPHA[role] * scale)
            return (0, 0, 0, alpha) if alpha else (0, 0, 0, 0)

        out = ICON_DIR / f"mushroom-{name}.png"
        rasterize(green_rects, mono_ink, args.size).save(out)
        print(f"wrote {out}")

    if args.color:
        for color, svg_name in COLOR_SVGS.items():
            rects = parse_svg(SVG_DIR / svg_name)
            out = ICON_DIR / f"mushroom-{color}.png"
            rasterize(rects, hex_to_rgba, args.size).save(out)
            print(f"wrote {out}")
        # gray = green geometry with the cap re-inked neutral
        def gray_ink(fill):
            if ROLE_BY_FILL.get(fill) == "cap":
                return (156, 163, 175, 255)  # Tailwind gray-400
            return hex_to_rgba(fill)

        out = ICON_DIR / "mushroom-gray.png"
        rasterize(green_rects, gray_ink, args.size).save(out)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
