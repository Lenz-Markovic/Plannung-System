"""
Coordinates of buildings, orders and home addresses.

Every address is geocoded ONCE with TomTom and stored in the database
(fields from core.models.GeocodedAddress). Without a TomTom key the centre
of the postcode is used as a rough estimate (table PLZKOORD from the
prototype, planning/data/zip_coordinates.json); such positions are never
stored and plans based on them cannot be confirmed.
"""

import json
from functools import lru_cache
from pathlib import Path

from django.utils import timezone

from core.models import GeocodeStatus

from .tomtom import TomTomError

ZIP_FILE = Path(__file__).parent / "data" / "zip_coordinates.json"


@lru_cache(maxsize=1)
def _zip_table():
    return json.loads(ZIP_FILE.read_text(encoding="utf-8"))


def zip_centre(zip_code):
    point = _zip_table().get(str(zip_code or ""))
    return tuple(point) if point else None


def geocode(obj, client):
    """Look the address up at TomTom once and store it. Returns a list of warnings."""
    if not obj.needs_geocoding and obj.latitude is not None:
        return address_warnings(obj)
    try:
        result = client.geocode(obj.full_address)
    except TomTomError:
        obj.geocode_status = GeocodeStatus.FAILED
        obj.geocoded_address = obj.full_address
        _save_quietly(obj)
        raise
    obj.latitude, obj.longitude = result.latitude, result.longitude
    obj.geocode_status = GeocodeStatus.OK if result.match_type == "Point Address" else GeocodeStatus.APPROXIMATE
    obj.geocoded_at = timezone.now()
    obj.geocoded_address = obj.full_address
    _save_quietly(obj)
    warnings = address_warnings(obj)
    if obj.zip_code and result.zip_code and obj.zip_code not in [z.strip() for z in result.zip_code.split(",")]:
        warnings.append(f"PLZ passt nicht: eingetragen {obj.zip_code}, TomTom findet {result.zip_code} ({result.label}). Adresse prüfen.")
    return warnings


def address_warnings(obj):
    if obj.geocode_status == GeocodeStatus.APPROXIMATE:
        return ["Hausnummer bei TomTom nicht hinterlegt – gerechnet mit Straßen- bzw. Ortsmitte."]
    return []


def _save_quietly(obj):
    """Save only the coordinates, without an entry in the change history."""
    obj.skip_history_when_saving = True
    try:
        obj.save(update_fields=["latitude", "longitude", "geocode_status", "geocoded_at", "geocoded_address"])
    finally:
        del obj.skip_history_when_saving


def position(obj, client):
    """(lat, lon), source ('tomtom' / 'zip' / None) and warnings for one address."""
    if client is not None:
        try:
            warnings = geocode(obj, client)
            return (obj.latitude, obj.longitude), "tomtom", warnings
        except TomTomError as error:
            centre = zip_centre(obj.zip_code)
            return centre, ("zip" if centre else None), [str(error)]
    if obj.latitude is not None and not obj.needs_geocoding:
        return (obj.latitude, obj.longitude), "tomtom", address_warnings(obj)
    centre = zip_centre(obj.zip_code)
    return centre, ("zip" if centre else None), []
