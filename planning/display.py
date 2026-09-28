"""Presentation helpers for the planning pages."""

from dataclasses import dataclass

WIDTH, HEIGHT, PAD = 460, 340, 28


@dataclass
class SketchPoint:
    x: float
    y: float
    number: int
    colour: str
    label: str


@dataclass
class Sketch:
    points: list
    lines: list  # (x1, y1, x2, y2) between consecutive stops


def route_sketch(stops):
    """Positions for a simple SVG sketch of the route (no map tiles needed).

    Green = start, red = end, blue = stops in between (colours as in the
    prototype's map). Stops without coordinates are left out.
    """
    placed = [s for s in stops if s.point]
    if not placed:
        return Sketch([], [])
    lats = [s.point[0] for s in placed]
    lons = [s.point[1] for s in placed]
    lat_span = max(lats) - min(lats) or 0.01
    lon_span = max(lons) - min(lons) or 0.01
    scale = min((WIDTH - 2 * PAD) / lon_span, (HEIGHT - 2 * PAD) / (lat_span * 1.5))  # 1.5: longitude is "narrower"
    points = []
    last = len(stops) - 1
    for stop in placed:
        x = PAD + (stop.point[1] - min(lons)) * scale
        y = HEIGHT - PAD - (stop.point[0] - min(lats)) * scale * 1.5
        colour = "#2E8B57" if stop.index == 0 else ("#C8372D" if stop.index == last else "#2F6FB5")
        points.append(SketchPoint(round(x, 1), round(y, 1), stop.index + 1, colour, stop.target.street))
    lines = [(a.x, a.y, b.x, b.y) for a, b in zip(points, points[1:])]
    return Sketch(points, lines)
