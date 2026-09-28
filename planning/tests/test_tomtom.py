"""TomTom client and geocoding with a fake TomTom (no key, no internet needed)."""

import datetime

import pytest
from django.core.cache import cache

from buildings.models import Building
from core.models import GeocodeStatus
from planning.geocoding import position, zip_centre
from planning.tomtom import TomTomClient, TomTomError, traffic_warnings


class FakeResponse:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.ok = data, status, status < 400

    def json(self):
        return self._data


class FakeSession:
    """Records the requests and answers like TomTom."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get(self, url, params, timeout):
        self.calls.append((url, params))
        for part, answer in self.answers.items():
            if part in url:
                return answer
        raise AssertionError(f"unexpected url {url}")


GEOCODE = FakeResponse({"results": [{"position": {"lat": 48.6, "lon": 8.9}, "type": "Point Address",
                                     "address": {"freeformAddress": "Mörikeweg 8, 71154 Nufringen", "postalCode": "71154"}}]})
ROUTE = FakeResponse({"routes": [{"summary": {"travelTimeInSeconds": 1201, "lengthInMeters": 15000},
                                  "sections": [{"sectionType": "TRAFFIC", "simpleCategory": "ROAD_WORK", "delayInSeconds": 240}],
                                  "legs": [{"points": [{"latitude": 48.6, "longitude": 8.9}]}]}]})


@pytest.fixture(autouse=True)
def empty_cache():
    cache.clear()


def test_route_sends_departure_and_key_only_to_tomtom():
    session = FakeSession({"calculateRoute": ROUTE})
    client = TomTomClient("SECRET", session)
    departure = datetime.datetime(2030, 1, 14, 9, 30, tzinfo=datetime.timezone.utc)
    leg = client.route((48.6, 8.9), (48.7, 9.0), departure)
    url, params = session.calls[0]
    assert url.startswith("https://api.tomtom.com/routing/1/calculateRoute/48.6,8.9:48.7,9.0/json")
    assert params["departAt"] == "2030-01-14T09:30:00" and params["key"] == "SECRET"
    assert (leg.seconds, leg.meters) == (1201, 15000)
    assert leg.warnings == ["Baustelle auf der Strecke gemeldet (+4 min) – Stand heute, am Plantag evtl. anders."]
    client.route((48.6, 8.9), (48.7, 9.0), departure)
    assert len(session.calls) == 1  # second time from the cache


def test_rejected_key_gives_german_message():
    client = TomTomClient("WRONG", FakeSession({"calculateRoute": FakeResponse({}, 403)}))
    with pytest.raises(TomTomError, match="lehnt den API-Schlüssel ab"):
        client.route((48.6, 8.9), (48.7, 9.0), datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc))


def test_traffic_warnings_ignore_short_jams():
    assert traffic_warnings([{"sectionType": "TRAFFIC", "simpleCategory": "JAM", "delayInSeconds": 120}]) == []


@pytest.mark.django_db
def test_building_is_geocoded_once_and_stored():
    building = Building.objects.create(source_system="bfw_main", file_number="0798615", stichtag=datetime.date(2026, 12, 31),
                                       street="Mörikeweg 8", zip_code="71154", city="Nufringen")
    session = FakeSession({"geocode": GEOCODE})
    client = TomTomClient("KEY", session)
    assert position(building, client)[:2] == ((48.6, 8.9), "tomtom")
    assert position(building, client)[:2] == ((48.6, 8.9), "tomtom")
    assert len(session.calls) == 1  # only once
    building.refresh_from_db()
    assert building.geocode_status == GeocodeStatus.OK
    assert building.history.count() == 1  # geocoding does not clutter the change history


@pytest.mark.django_db
def test_without_key_the_postcode_centre_is_used():
    building = Building(zip_code="71154", street="x", city="y")
    point, source, _ = position(building, None)
    assert source == "zip" and point == zip_centre("71154")
