"""Presentation helpers for the planning pages."""

from dataclasses import dataclass

WIDTH, HEIGHT, PAD = 460, 340, 28
START, STOP, END = "#2E8B57", "#2F6FB5", "#C8372D"  # green, blue, red as in the prototype


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
        colour = START if stop.index == 0 else (END if stop.index == last else STOP)
        points.append(SketchPoint(round(x, 1), round(y, 1), stop.index + 1, colour, stop.target.street))
    lines = [(a.x, a.y, b.x, b.y) for a, b in zip(points, points[1:])]
    return Sketch(points, lines)


def _colour(index, last):
    return START if index == 0 else (END if index == last else STOP)


def preview_map_data(stops):
    """Data for the TomTom map of the plan preview (static/js/route_map.js).

    Lines follow the roads where TomTom calculated the drive (leg points),
    otherwise a straight line between the two stops.
    """
    last = len(stops) - 1
    pins, lines = [], []
    for stop in stops:
        if not stop.point:
            continue
        pins.append({"n": stop.index + 1, "lat": stop.point[0], "lon": stop.point[1],
                     "colour": _colour(stop.index, last),
                     "label": f"{stop.target.street}, {stop.target.city}",
                     "time": stop.start.strftime("%H:%M") if stop.start else ""})
    for stop, following in zip(stops, stops[1:]):
        if stop.points:
            lines.append(stop.points)
        elif stop.point and following.point:
            lines.append([[stop.point[1], stop.point[0]], [following.point[1], following.point[0]]])
    return {"pins": pins, "lines": lines}


def tour_map_data(stops):
    """The same for a saved tour (TourStop objects): straight lines between the stops.

    Addresses not geocoded yet are placed at the centre of their postcode.
    """
    from .geocoding import zip_centre  # local import: geocoding imports the TomTom client

    stops = list(stops)
    last = len(stops) - 1
    pins = []
    for index, stop in enumerate(stops):
        target = stop.building or stop.installation_order
        point = (target.latitude, target.longitude) if target and target.latitude is not None else None
        point = point or (zip_centre(target.zip_code) if target else None)
        if point is None:
            continue
        pins.append({"n": index + 1, "lat": point[0], "lon": point[1],
                     "colour": _colour(index, last), "label": f"{target.street}, {target.city}",
                     "time": stop.start_time.strftime("%H:%M") if stop.start_time else "",
                     "done": bool(stop.done_at)})
    lines = [[[a["lon"], a["lat"]] for a in pins]] if len(pins) > 1 else []
    return {"pins": pins, "lines": lines}
