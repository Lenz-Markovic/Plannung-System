"""
Driving times (spec section 7: "Die TomTom-Fahrzeit wird immer auf 5 Minuten aufgerundet").

Ports from the prototype:
- planned_drive_minutes: rounding of real TomTom times (planRechneTomTom)
- estimate_drive_minutes: rough estimate without TomTom (planFahrzeit);
  a plan with estimates can never be confirmed.
"""

import math

EARTH_RADIUS_KM = 6371


def planned_drive_minutes(seconds, meters):
    """TomTom seconds -> minutes in the plan: rounded UP to 5, at least 5.

    Less than 50 m (same address) counts as 0 minutes.
    """
    if meters < 50:
        return 0
    exact_minutes = math.ceil(seconds / 60)
    return max(5, math.ceil(exact_minutes / 5) * 5)


def distance_km(a, b):
    """Straight-line distance between two (lat, lon) points (haversine)."""
    if a is None or b is None:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(h))


def estimate_drive_minutes(a, b):
    """Estimate: straight line + 30 % detour at 45 km/h, rounded up to 5 minutes."""
    km = distance_km(a, b)
    if km is None:
        return 10
    if km < 0.3:
        return 5
    return max(5, math.ceil(km * 1.3 / 45 * 60 / 5) * 5)
