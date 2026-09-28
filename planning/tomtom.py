"""
TomTom client: the ONLY place where our server talks to TomTom.

The API key comes from settings.TOMTOM_API_KEY (environment variable /
.env). It is sent from here to TomTom and never to a browser.

Used APIs (same as the prototype):
- Search / Geocoding   address -> coordinates (done once per address)
- Routing              driving time between two points, with departure
                       time -> TomTom uses historical traffic for that
                       weekday and time
- Routing with computeBestOrder   best order of many stops
- Map Display (raster tiles)      the map images; the browser loads them from
                                  OUR server (planning/views.map_tile), which
                                  adds the key here - so the key stays secret
"""

import datetime
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import dotenv_values
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

BASE_URL = "https://api.tomtom.com"
TIMEOUT_SECONDS = 15
LEG_CACHE_SECONDS = 24 * 3600
TILE_CACHE_SECONDS = 7 * 24 * 3600  # map images hardly change
MAX_ZOOM = 18


class TomTomError(Exception):
    """Something went wrong; the message is German and can be shown to the user."""


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    label: str
    match_type: str  # "Point Address" = exact house number
    zip_code: str


@dataclass(frozen=True)
class Leg:
    seconds: int
    meters: int
    warnings: list = field(default_factory=list)
    points: list = field(default_factory=list)  # [[lon, lat], ...] for the map


class TomTomClient:
    def __init__(self, api_key, session=None):
        self.api_key = api_key
        self.session = session or requests.Session()

    # --- low level ----------------------------------------------------------

    def _get(self, path, params, attempt=0):
        try:
            response = self.session.get(BASE_URL + path, params={**params, "key": self.api_key}, timeout=TIMEOUT_SECONDS)
        except requests.RequestException:
            if attempt < 1:
                return self._get(path, params, attempt + 1)
            raise TomTomError("TomTom ist nicht erreichbar. Internetverbindung prüfen und erneut versuchen.")
        if response.status_code == 429 and attempt < 3:
            time.sleep(1.2 * (attempt + 1))  # too many requests: wait a little and try again
            return self._get(path, params, attempt + 1)
        if response.status_code in (401, 403):
            raise TomTomError(f"TomTom lehnt den API-Schlüssel ab (HTTP {response.status_code}). Schlüssel in der .env prüfen.")
        if not response.ok:
            raise TomTomError(f"TomTom-Fehler HTTP {response.status_code}{_error_text(response)}.")
        return response.json()

    # --- map images ------------------------------------------------------------

    def map_tile(self, z, x, y):
        """One 256x256 PNG map image (standard web map tile numbering z/x/y)."""
        if not (0 <= z <= MAX_ZOOM and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
            raise TomTomError("Kachel außerhalb der Karte.")
        cache_key = f"tt-tile-{z}-{x}-{y}"
        cached = cache.get(cache_key)
        if cached:
            return cached
        try:
            response = self.session.get(
                f"{BASE_URL}/map/1/tile/basic/main/{z}/{x}/{y}.png",
                params={"key": self.api_key, "tileSize": 256, "language": "de-DE"}, timeout=TIMEOUT_SECONDS)
        except requests.RequestException:
            raise TomTomError("TomTom ist nicht erreichbar.")
        if response.status_code in (401, 403):
            raise TomTomError(f"TomTom lehnt den API-Schlüssel ab (HTTP {response.status_code}) – ist die Map Display API freigeschaltet?")
        if not response.ok:
            raise TomTomError(f"TomTom-Fehler HTTP {response.status_code}.")
        cache.set(cache_key, response.content, TILE_CACHE_SECONDS)
        return response.content

    # --- geocoding ------------------------------------------------------------

    def geocode(self, address):
        data = self._get(f"/search/2/geocode/{quote(address)}.json",
                         {"countrySet": "DE", "limit": 1, "language": "de-DE"})
        results = data.get("results") or []
        if not results:
            raise TomTomError(f"TomTom findet die Adresse nicht: {address}")
        first = results[0]
        return GeocodeResult(
            latitude=first["position"]["lat"],
            longitude=first["position"]["lon"],
            label=first.get("address", {}).get("freeformAddress", address),
            match_type=first.get("type", ""),
            zip_code=first.get("address", {}).get("postalCode", ""),
        )

    # --- routing ----------------------------------------------------------------

    def route(self, origin, destination, departure):
        """Drive from origin to destination ((lat, lon) tuples), leaving at `departure`.

        TomTom only accepts future departure times; for a past date we
        calculate with today's traffic and add a warning (as the prototype).
        Results are cached for a day, so reordering a plan is fast.
        """
        cache_key = "tt-leg-" + hashlib.sha1(f"{origin}|{destination}|{departure:%Y-%m-%dT%H:%M}".encode()).hexdigest()
        cached = cache.get(cache_key)
        if cached:
            return cached

        params = {"travelMode": "car", "routeType": "fastest", "traffic": "true",
                  "sectionType": "traffic", "language": "de-DE"}
        warnings = []
        if departure > timezone.now():
            params["departAt"] = departure.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            warnings.append("Datum liegt in der Vergangenheit – gerechnet mit dem Verkehr von heute.")
        locations = f"{origin[0]},{origin[1]}:{destination[0]},{destination[1]}"
        data = self._get(f"/routing/1/calculateRoute/{locations}/json", params)

        route = data["routes"][0]
        warnings += traffic_warnings(route.get("sections", []))
        points = [[p["longitude"], p["latitude"]] for leg in route.get("legs", []) for p in leg.get("points", [])]
        leg = Leg(seconds=route["summary"]["travelTimeInSeconds"], meters=route["summary"]["lengthInMeters"],
                  warnings=warnings, points=points)
        cache.set(cache_key, leg, LEG_CACHE_SECONDS)
        return leg

    def best_order(self, points):
        """Best order of the points BETWEEN the first and the last one.

        points: list of (lat, lon); needs at least 4 points to be useful.
        Returns indices into points[1:-1], e.g. [2, 0, 1].
        """
        locations = ":".join(f"{lat},{lon}" for lat, lon in points)
        data = self._get(f"/routing/1/calculateRoute/{locations}/json",
                         {"travelMode": "car", "routeType": "fastest", "computeBestOrder": "true",
                          "traffic": "false", "routeRepresentation": "summaryOnly"})
        waypoints = data.get("optimizedWaypoints") or []
        order = [None] * len(waypoints)
        for waypoint in waypoints:
            order[waypoint["optimizedIndex"]] = waypoint["providedIndex"]
        return order


def _error_text(response):
    """TomTom's own error message, e.g. ': departAt must be in the future'."""
    try:
        data = response.json()
    except ValueError:
        return ""
    message = ((data.get("detailedError") or {}).get("message") or (data.get("error") or {}).get("description")
               or data.get("errorText") or "")
    return f": {message}" if message else ""


def traffic_warnings(sections):
    """Roadworks, closures and long jams on the route (as in the prototype)."""
    warnings = []
    for section in sections:
        if section.get("sectionType") != "TRAFFIC":
            continue
        delay = section.get("delayInSeconds") or 0
        plus = f" (+{round(delay / 60)} min)" if delay else ""
        category = section.get("simpleCategory")
        if category == "ROAD_WORK":
            warnings.append(f"Baustelle auf der Strecke gemeldet{plus} – Stand heute, am Plantag evtl. anders.")
        elif category == "ROAD_CLOSURE":
            warnings.append(f"Straßensperrung auf der Strecke gemeldet{plus} – Stand heute.")
        elif category == "JAM" and delay >= 300:
            warnings.append(f"Stau gemeldet{plus} – Stand heute.")
    return warnings


def current_api_key():
    """The TomTom key: read FRESH from .env on every call, else from the settings.

    Reading the file every time means a new key in .env works at once -
    no server restart needed - and an empty or old TOMTOM_API_KEY variable
    in the environment cannot hide the key in .env. On a real server
    without a .env file, the environment variable (settings) is used.
    """
    env_file = Path(settings.BASE_DIR) / ".env"
    if env_file.exists():
        value = (dotenv_values(env_file).get("TOMTOM_API_KEY") or "").strip().strip("\"'").strip()
        if value:
            return value
    return settings.TOMTOM_API_KEY


def get_client():
    """A client if a key is configured, otherwise None (then only estimates are possible)."""
    key = current_api_key()
    return TomTomClient(key) if key else None


def local_datetime(date, clock_time):
    """date + time -> timezone-aware datetime in Europe/Berlin."""
    return timezone.make_aware(datetime.datetime.combine(date, clock_time))
