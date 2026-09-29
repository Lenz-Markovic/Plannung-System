"""Building list: permissions, filters, sorting, HTMX parts, display helpers."""

import datetime
import io

import pytest
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.urls import reverse

from buildings.display import device_chips, short_region
from buildings.models import Building, SourceSystem
from core import roles
from core.templatetags.core_tags import minutes

pytestmark = pytest.mark.django_db


@pytest.fixture
def office_client(demo_import, client):
    """Logged in as Sachbearbeitung, with the demo data imported."""
    user = User.objects.create_user(username="sb", password="x")
    user.groups.add(Group.objects.get(name=roles.PROCESSING))
    client.force_login(user)
    return client


def count_in(response):
    return response.context["page"].paginator.count


def test_reader_may_not_open_the_list(client):
    call_command("setup_roles", stdout=io.StringIO())
    reader = User.objects.create_user(username="abl", password="x")
    reader.groups.add(Group.objects.get(name=roles.READER))
    client.force_login(reader)
    assert client.get(reverse("buildings:list")).status_code == 403


def test_office_start_page_redirects_to_list(office_client):
    assert office_client.get(reverse("home"))["Location"] == reverse("buildings:list")


def test_full_list_and_kpis(office_client):
    response = office_client.get(reverse("buildings:list"))
    assert response.status_code == 200
    summary = response.context["summary"]
    # same numbers as the KPI tiles of the prototype
    assert (summary["total"], summary["released"], summary["open"], summary["rework"]) == (240, 46, 168, 26)
    assert (summary["hkv"], summary["apartments"], summary["gateways"]) == (9734, 1857, 24)
    assert len(response.context["rows"]) == 100  # first page


@pytest.mark.parametrize(
    "params, expected",
    [
        ({"region": "Region Calw"}, 28),
        ({"status": "rework"}, 26),
        ({"source_system": "ceos"}, 31),
        ({"gateway": "1"}, 24),
        ({"documents": "ja"}, 19),
        ({"montage": "any"}, 28),
        ({"montage": "krit"}, 4),  # the prototype also shows 4 "Konflikte"
        ({"q": "RE90108"}, 1),
        ({"q": "Nufringen"}, 3),
        ({"property_manager": "__none"}, 0),
    ],
)
def test_filters(office_client, params, expected):
    assert count_in(office_client.get(reverse("buildings:list"), params)) == expected


def test_sort_by_hkv_descending(office_client):
    rows = office_client.get(reverse("buildings:list"), {"sort": "-hkv"}).context["rows"]
    counts = [b.hkv_count for b in rows]
    assert counts == sorted(counts, reverse=True)


def test_unknown_sort_is_ignored(office_client):
    assert office_client.get(reverse("buildings:list"), {"sort": "password"}).status_code == 200


def test_htmx_request_returns_only_results_with_clean_url(office_client):
    response = office_client.get(
        reverse("buildings:list"), {"q": "", "region": "Region Calw"},
        HTTP_HX_REQUEST="true", HTTP_HX_TARGET="results",
    )
    html = response.content.decode()
    assert "<html" not in html and "class=\"kpis\"" in html
    assert response["HX-Push-Url"] == reverse("buildings:list") + "?region=Region+Calw"


def test_next_page_and_detail_row(office_client):
    rows = office_client.get(reverse("buildings:rows"), {"page": 3})
    assert len(rows.context["rows"]) == 40  # 240 = 100 + 100 + 40
    building = Building.objects.get(file_number="0798792")
    opened = office_client.get(reverse("buildings:row", args=[building.pk]), {"open": "1"}).content.decode()
    assert f'id="det-{building.pk}"' in opened and "Montageauftrag RE90108" in opened
    closed = office_client.get(reverse("buildings:row", args=[building.pk]), {"open": "0"}).content.decode()
    assert 'hx-swap-oob="delete"' in closed


def test_schedule_of_conflict_building(office_client):
    """0798792: reading 03.12. (Hofmann), installation RE90108 07.12. (Kaiser) -> critical."""
    response = office_client.get(reverse("buildings:list"), {"q": "0798792"})
    schedule = response.context["rows"][0].schedule
    assert schedule.reading.date == datetime.date(2026, 12, 3)
    assert schedule.first_installation.people == ["Kaiser"]
    assert schedule.css == "crit"
    assert schedule.check.message == "Montage 4 Tage NACH Ablesung — Tour verschieben"


def test_device_chips_and_labels():
    building = Building(source_system=SourceSystem.CEOS, wmz_count=3, wwz_count=6, has_rwm=True, rwm_count=12)
    # CEOS: WMZ is hidden (included in WWZ); empty groups are left out
    assert device_chips(building) == [[("6 WWZ", "water")], [("12 RWM", "safety")]]
    assert device_chips(Building(source_system=SourceSystem.BFW_MAIN)) == []
    assert short_region("Region Böblingen/Sindelfingen/Holzgerlingen") == "Böblingen"
    assert short_region("Region Schorndorf (Rems-Murr)") == "Schorndorf"
    assert (minutes(80), minutes(45)) == ("1h 20min", "45min")
