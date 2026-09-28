"""Mobile day plan "Mein Tag", reader proposals and the near-live building list."""

import datetime
import io
import time

import pytest
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from buildings.models import Building, BuildingStatus, SourceSystem
from core import roles
from planning.models import Employee, StopKind, Tour, TourStatus, TourStop

pytestmark = pytest.mark.django_db


def user_with(name, role):
    user = User.objects.create_user(username=name, password="x")
    user.groups.add(Group.objects.get(name=role))
    return user


def login(user):
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def day():
    """Reader 'abl' with a tour of two stops today; a second reader 'other'."""
    call_command("setup_roles", stdout=io.StringIO())
    reader = user_with("abl", roles.READER)
    employee = Employee.objects.create(user=reader, short_name="Abl")
    other = user_with("other", roles.READER)
    Employee.objects.create(user=other, short_name="Other")
    tour = Tour.objects.create(employee=employee, date=timezone.localdate(), status=TourStatus.CONFIRMED,
                               confirmed_at=timezone.now())
    stops = []
    for position, number in enumerate(["0798615", "0798616"], start=1):
        building = Building.objects.create(
            source_system=SourceSystem.BFW_MAIN, file_number=number, stichtag=datetime.date(2026, 12, 31),
            street=f"Mörikeweg {position}", zip_code="71154", city="Nufringen",
            remark="Hausmeister Tel. 0711 123456", status=BuildingStatus.OPEN)
        stops.append(TourStop.objects.create(tour=tour, position=position, kind=StopKind.READING, building=building,
                                             start_time=datetime.time(8 + position, 0), work_minutes=30))
    return {"reader": reader, "other": other, "tour": tour, "stops": stops}


def test_reader_starts_in_his_day_plan(day):
    response = login(day["reader"]).get(reverse("home"))
    assert response.url == reverse("planning:my_day")


def test_day_page_shows_stops_with_navigation_and_phone_links(day):
    response = login(day["reader"]).get(reverse("planning:my_day"))
    html = response.content.decode()
    assert response.status_code == 200
    assert "Heute" in html and "Mörikeweg 1" in html and "0 von 2 erledigt" in html
    assert "https://www.google.com/maps/dir/?api=1&amp;destination=M%C3%B6rikeweg+1" in html
    assert 'href="tel:0711123456"' in html
    assert "📱 Mein Tag" in html
    assert 'Status vorschlagen …' in html and '<option value="rework">Nacharbeit</option>' in html


def test_mark_stop_done_updates_progress_and_finishes_tour(day):
    client = login(day["reader"])
    first, second = day["stops"]
    response = client.post(reverse("planning:stop_done", args=[first.pk]), {"done": "1"})
    html = response.content.decode()
    assert "1 von 2 erledigt" in html and 'hx-swap-oob="true"' in html and "Stopp erledigt" in html
    first.refresh_from_db()
    assert first.done_at and first.done_by == day["reader"]

    client.post(reverse("planning:stop_done", args=[second.pk]), {"done": "1"})
    day["tour"].refresh_from_db()
    assert day["tour"].status == TourStatus.DONE

    client.post(reverse("planning:stop_done", args=[second.pk]), {"done": "0"})  # undo
    day["tour"].refresh_from_db()
    assert day["tour"].status == TourStatus.CONFIRMED


def test_reader_cannot_touch_someone_elses_stops(day):
    client = login(day["other"])
    stop = day["stops"][0]
    assert client.post(reverse("planning:stop_done", args=[stop.pk]), {"done": "1"}).status_code == 403
    assert client.post(reverse("planning:stop_note", args=[stop.pk]), {"field_note": "x"}).status_code == 403
    assert client.post(reverse("planning:stop_propose", args=[stop.pk]), {"status": "rework"}).status_code == 403
    # ?person= is only for the office: the other reader still sees their own (empty) day
    html = client.get(reverse("planning:my_day") + f"?person={day['tour'].employee.pk}").content.decode()
    assert "kein Fahrplan" in html and "Mörikeweg" not in html


def test_field_note_is_saved(day):
    stop = day["stops"][0]
    login(day["reader"]).post(reverse("planning:stop_note", args=[stop.pk]), {"field_note": "  Mieter nicht da "})
    stop.refresh_from_db()
    assert stop.field_note == "Mieter nicht da"


def test_proposal_and_office_accepts_it(day):
    building = day["stops"][0].building
    login(day["reader"]).post(reverse("planning:stop_propose", args=[day["stops"][0].pk]), {"status": "rework"})
    building.refresh_from_db()
    assert (building.status, building.proposed_status, building.proposed_status_by) == ("open", "rework", day["reader"])

    office = login(user_with("sb", roles.PROCESSING))
    row = office.get(reverse("buildings:row", args=[building.pk])).content.decode()
    assert "Vorschlag: Nacharbeit" in row and "übernehmen" in row
    response = office.post(reverse("buildings:update", args=[building.pk]), {"accept_proposal": "1"})
    assert "Vorschlag übernommen" in response.content.decode()
    building.refresh_from_db()
    assert (building.status, building.proposed_status) == ("rework", "")


def test_day_check_reloads_only_when_the_tour_changed(day):
    client = login(day["reader"])
    tour = day["tour"]
    url = reverse("planning:my_day_check", args=[tour.pk])
    assert client.get(url, {"version": tour.version}).status_code == 204
    Tour.objects.filter(pk=tour.pk).update(version=tour.version + 1)  # the office saved the plan
    assert client.get(url, {"version": tour.version})["HX-Refresh"] == "true"


def test_office_can_look_at_any_day(day):
    office = login(user_with("dispo", roles.DISPATCHER))
    html = office.get(reverse("planning:my_day") + f"?person={day['tour'].employee.pk}").content.decode()
    assert "Mörikeweg 1" in html and "Stopp erledigt" in html  # dispatchers may tick stops, too


def test_management_may_look_but_not_tick(day):
    html = login(user_with("chef", roles.MANAGEMENT)).get(
        reverse("planning:my_day") + f"?person={day['tour'].employee.pk}").content.decode()
    assert "Mörikeweg 1" in html and "Stopp erledigt" not in html


def test_user_without_tour_rights_gets_403(day):
    nobody = User.objects.create_user(username="nobody")
    assert login(nobody).get(reverse("planning:my_day")).status_code == 403


def test_live_list_sends_rows_changed_by_others(day):
    office = login(user_with("sb", roles.PROCESSING))
    since = time.time()
    # nothing changed by others yet
    response = office.get(reverse("buildings:changes"), {"seit": since})
    assert 'id="live"' in response.content.decode() and "toast" not in response.content.decode()

    # a reader ticks off a stop -> the office list gets the row and a toast
    login(day["reader"]).post(reverse("planning:stop_done", args=[day["stops"][0].pk]), {"done": "1"})
    html = office.get(reverse("buildings:changes"), {"seit": since}).content.decode()
    assert f'id="row-{day["stops"][0].building.pk}" hx-swap-oob="true"' in html
    assert "1 Liegenschaft von abl geändert" in html

    # own changes are not reported back
    since = time.time()
    office.post(reverse("buildings:update", args=[day["stops"][1].building.pk]), {"note": "neu"})
    assert "toast" not in office.get(reverse("buildings:changes"), {"seit": since}).content.decode()


def test_live_list_ignores_a_broken_timestamp(day):
    office = login(user_with("sb", roles.PROCESSING))
    assert office.get(reverse("buildings:changes"), {"seit": "abc"}).status_code == 200
