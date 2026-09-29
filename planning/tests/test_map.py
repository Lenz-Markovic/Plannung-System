"""TomTom map: map images through our server, the key never reaches the browser."""

import datetime
import io
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from buildings.models import Building, SourceSystem
from core import roles
from planning import tomtom
from planning.display import preview_map_data, tour_map_data
from planning.models import Employee, StopKind, Tour, TourStop
from planning.tomtom import TomTomClient, TomTomError

pytestmark = pytest.mark.django_db
SECRET = "SECRETsecretSECRETsecret12345678"
PNG = b"\x89PNG fake image"


class TileSession:
    """Answers like the TomTom Map Display API and records the requests."""

    def __init__(self, status=200):
        self.status, self.calls = status, []

    def get(self, url, params, timeout):
        self.calls.append((url, params))
        return SimpleNamespace(status_code=self.status, ok=self.status < 400, content=PNG)


@pytest.fixture
def key(settings, tmp_path):
    """A TomTom key in the server settings only (no .env file in tmp_path)."""
    settings.BASE_DIR = tmp_path
    settings.TOMTOM_API_KEY = SECRET
    cache.clear()
    return SECRET


@pytest.fixture
def dispatcher(client):
    call_command("setup_roles", stdout=io.StringIO())
    user = User.objects.create_user(username="dispo")
    user.groups.add(Group.objects.get(name=roles.DISPATCHER))
    client.force_login(user)
    return client


@pytest.fixture
def tour():
    reader = User.objects.create_user(username="abl")
    employee = Employee.objects.create(user=reader, short_name="Abl")
    tour = Tour.objects.create(employee=employee, date=timezone.localdate())
    for position, (lat, zip_code) in enumerate([(48.6, "71154"), (None, "72202")], start=1):
        building = Building.objects.create(
            source_system=SourceSystem.BFW_MAIN, file_number=f"079861{position}", stichtag=datetime.date(2026, 12, 31),
            street=f"Weg {position}", zip_code=zip_code, city="Ort", latitude=lat, longitude=8.9 if lat else None)
        TourStop.objects.create(tour=tour, position=position, kind=StopKind.READING, building=building,
                                start_time=datetime.time(8 + position, 0))
    return tour


# --- TomTom client -------------------------------------------------------------

def test_tile_is_fetched_with_the_key_and_cached(key):
    session = TileSession()
    client = TomTomClient(key, session)
    assert client.map_tile(8, 134, 88) == PNG
    assert client.map_tile(8, 134, 88) == PNG  # second time from the cache
    assert len(session.calls) == 1
    url, params = session.calls[0]
    assert url.endswith("/map/1/tile/basic/main/8/134/88.png") and params["key"] == SECRET


@pytest.mark.parametrize("z, x, y", [(19, 0, 0), (2, 4, 0), (2, 0, -1)])
def test_tiles_outside_the_map_are_refused(key, z, x, y):
    with pytest.raises(TomTomError):
        TomTomClient(key, TileSession()).map_tile(z, x, y)


def test_rejected_key_is_reported(key):
    with pytest.raises(TomTomError, match="Map Display"):
        TomTomClient(key, TileSession(status=403)).map_tile(1, 0, 0)


# --- tile view -----------------------------------------------------------------

def test_tile_view_needs_login(client, key):
    assert client.get(reverse("planning:map_tile", args=[8, 134, 88])).status_code == 302


def test_tile_view_without_key_is_empty(dispatcher, settings, tmp_path):
    settings.BASE_DIR, settings.TOMTOM_API_KEY = tmp_path, ""
    assert dispatcher.get(reverse("planning:map_tile", args=[8, 134, 88])).status_code == 404


def test_tile_view_passes_the_image_on_without_the_key(dispatcher, key, monkeypatch):
    monkeypatch.setattr(tomtom.requests, "Session", TileSession)
    response = dispatcher.get(reverse("planning:map_tile", args=[8, 134, 88]))
    assert response.status_code == 200 and response["Content-Type"] == "image/png"
    assert response.content == PNG and SECRET.encode() not in response.content
    assert "max-age" in response["Cache-Control"]


def test_tile_view_answers_404_when_tomtom_fails(dispatcher, key, monkeypatch):
    monkeypatch.setattr(tomtom.requests, "Session", lambda: TileSession(status=500))
    assert dispatcher.get(reverse("planning:map_tile", args=[8, 134, 88])).status_code == 404


# --- pages -----------------------------------------------------------------------

def test_pages_show_the_map_but_never_the_key(dispatcher, key, tour):
    for url in [reverse("planning:tour_detail", args=[tour.pk])]:
        html = dispatcher.get(url).content.decode()
        assert 'class="route-map' in html and "/planung/karte/0/0/0.png" in html, url
        assert SECRET not in html, url


def test_without_key_the_side_panel_has_no_map(dispatcher, settings, tmp_path, tour):
    settings.BASE_DIR, settings.TOMTOM_API_KEY = tmp_path, ""
    html = dispatcher.get(reverse("planning:tour_detail", args=[tour.pk])).content.decode()
    assert "route-map" not in html


# --- map data --------------------------------------------------------------------

def test_tour_map_data_places_missing_coordinates_at_the_postcode(tour):
    data = tour_map_data(tour.stops.select_related("building").order_by("position"))
    first, second = data["pins"]
    assert (first["n"], first["lat"], first["colour"], first["time"]) == (1, 48.6, "#2E8B57", "09:00")
    assert second["colour"] == "#C8372D" and second["lat"] is not None  # postcode centre of 72202
    assert data["lines"] == [[[8.9, 48.6], [second["lon"], second["lat"]]]]


def test_preview_map_data_uses_the_road_where_tomtom_calculated_it():
    target = SimpleNamespace(street="Weg", city="Ort")
    stops = [
        SimpleNamespace(index=0, point=(48.0, 8.0), target=target, start=datetime.time(8), points=[[8.0, 48.0], [8.5, 48.2], [9.0, 48.5]]),
        SimpleNamespace(index=1, point=(48.5, 9.0), target=target, start=None, points=[]),
        SimpleNamespace(index=2, point=(49.0, 9.5), target=target, start=None, points=[]),
    ]
    data = preview_map_data(stops)
    assert [p["colour"] for p in data["pins"]] == ["#2E8B57", "#2F6FB5", "#C8372D"]
    assert data["lines"] == [[[8.0, 48.0], [8.5, 48.2], [9.0, 48.5]],  # road from TomTom
                             [[9.0, 48.5], [9.5, 49.0]]]  # estimated: straight line
