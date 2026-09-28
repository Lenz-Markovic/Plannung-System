"""
Order of the stops in a tour (port of planSortiere() in the prototype).

Strategies:
- "far":   start with the stop farthest from the centre of all stops,
           then always drive to the nearest remaining stop
- "short": start with the first stop, then always the nearest one
           (the service can improve this further with TomTom's best order)
Stops without coordinates are appended at the end.
"""

from .drive_time import distance_km

FAR = "far"
SHORT = "short"
STRATEGIES = {FAR: "weitester Termin zuerst", SHORT: "kürzeste Gesamtstrecke"}


def order_stops(stops, strategy, coordinates):
    """Return the stops in driving order.

    stops: list of any objects; coordinates: function stop -> (lat, lon) or None
    """
    with_coordinates = [s for s in stops if coordinates(s) is not None]
    without = [s for s in stops if coordinates(s) is None]
    if len(with_coordinates) < 2:
        return list(stops)

    if strategy == FAR:
        points = [coordinates(s) for s in with_coordinates]
        centre = (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))
        start = max(with_coordinates, key=lambda s: distance_km(coordinates(s), centre))
    else:
        start = with_coordinates[0]

    route, rest = [start], [s for s in with_coordinates if s is not start]
    while rest:
        here = coordinates(route[-1])
        nearest = min(rest, key=lambda s: distance_km(here, coordinates(s)))
        rest.remove(nearest)
        route.append(nearest)
    return route + without
