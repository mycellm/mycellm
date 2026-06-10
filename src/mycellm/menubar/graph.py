"""AppKit rendering of the dropdown time graph (macOS only).

TPS as a Spore-Green line over Compute-Red activity bars, drawn into an
NSImage via a drawing handler so AppKit rasterizes at the display's scale
(crisp on retina). All geometry comes from `state.scale_series`, which is
platform-neutral and unit-tested.
"""

from __future__ import annotations

from AppKit import NSBezierPath, NSColor, NSImage, NSMakeRect

from .state import History, scale_series

WIDTH = 180.0
HEIGHT = 40.0

SPORE_GREEN = (0x22 / 255, 0xC5 / 255, 0x5E / 255)
COMPUTE_RED = (0xEF / 255, 0x44 / 255, 0x44 / 255)


def _color(rgb: tuple[float, float, float], alpha: float) -> NSColor:
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgb, alpha)


def render_graph(history: History) -> NSImage:
    tps_points = scale_series(history.tps, WIDTH, HEIGHT)
    active_points = scale_series(
        [float(a) for a in history.active], WIDTH, HEIGHT,
        vmax=4.0,  # bars use a fixed scale: 4 concurrent ≈ full height
    )

    def draw(rect) -> bool:
        # Baseline so an idle node still shows an axis, not an empty box.
        _color((0.5, 0.5, 0.5), 0.35).set()
        baseline = NSBezierPath.bezierPath()
        baseline.moveToPoint_((2.0, 2.0))
        baseline.lineToPoint_((WIDTH - 2.0, 2.0))
        baseline.setLineWidth_(1.0)
        baseline.stroke()

        if active_points:
            _color(COMPUTE_RED, 0.30).set()
            bar_width = max((WIDTH - 4.0) / max(len(active_points), 1) - 1.0, 1.0)
            for x, y in active_points:
                if y > 2.0:
                    NSBezierPath.fillRect_(NSMakeRect(x - bar_width / 2, 2.0,
                                                      bar_width, y - 2.0))

        if len(tps_points) >= 2:
            _color(SPORE_GREEN, 0.95).set()
            line = NSBezierPath.bezierPath()
            line.moveToPoint_(tps_points[0])
            for point in tps_points[1:]:
                line.lineToPoint_(point)
            line.setLineWidth_(1.5)
            line.stroke()
        return True

    return NSImage.imageWithSize_flipped_drawingHandler_(
        (WIDTH, HEIGHT), False, draw
    )
