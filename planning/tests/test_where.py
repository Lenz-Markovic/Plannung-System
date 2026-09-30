"""🛰 Wer ist wo? - the page and the positions by the Fahrplan."""

import datetime
import json

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core import roles
from planning import whereabouts
from planning.models import StopKind, Tour
from planning.rules.whereabouts import AT, BEFORE, DONE, DRIVING, minutes

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo(demo_import, monkeypatch):
    from planning import services
    monkeypatch.setattr(services, "get_client", lambda: None)


def client_for(role, name):
    u = User.objects.create_user(username=name)
    u.groups.add(Group.objects.get(name=role))
    c = Client()
    c.force_login(u)
    return c


def a_tour():
    """A plan with saved times: made with the planner (the imported demo plans have none)."""
    from planning import services
    from planning.models import Employee
    from buildings.models import Building

    employee = Employee.objects.filter(can_read=True, active=True).first()
    day = datetime.date(2031, 3, 4)
    ids = list(Building.objects.filter(status="open").values_list("pk", flat=True)[:3])
    tour = services.save_draft(services.create_draft(ids, employee, day, datetime.time(8), 30, "short"), None, confirm=False)
    return tour, list(tour.stops.order_by("position"))


def test_plans_without_saved_times_are_estimated(demo):
    tour = Tour.objects.filter(stops__start_time__isnull=True).distinct().first()
    p = [p for p in whereabouts.positions(tour.date, minutes(tour.start_time) + 1) if p.tour.pk == tour.pk][0]
    assert p.estimated and p.where.state == AT and p.stops[0].start == minutes(tour.start_time)


def test_positions_follow_the_plan(demo):
    tour, stops = a_tour()
    first = stops[0]
    found = {p.tour.pk: p for p in whereabouts.positions(tour.date, minutes(first.start_time) + 1)}
    assert found[tour.pk].where.state == AT and "Stopp 1" in found[tour.pk].text
    early = {p.tour.pk: p for p in whereabouts.positions(tour.date, 0)}
    assert early[tour.pk].where.state == BEFORE
    late = {p.tour.pk: p for p in whereabouts.positions(tour.date, 23 * 60 + 59)}
    assert late[tour.pk].where.state == DONE
    if len(stops) > 1 and first.departure_time and stops[1].start_time > first.departure_time:
        middle = minutes(first.departure_time) + 1
        assert found[tour.pk].where.state != DRIVING
        assert {p.tour.pk: p for p in whereabouts.positions(tour.date, middle)}[tour.pk].where.state in (DRIVING, "waiting")


def test_page_shows_the_list_and_map_data(demo):
    tour, stops = a_tour()
    c = client_for(roles.DISPATCHER, "dispo")
    t = stops[0].start_time.strftime("%H:%M")
    html = c.get(reverse("planning:where"), {"datum": tour.date.isoformat(), "zeit": t}).content.decode()
    assert "🛰 Wer ist wo?" in html and "keine GPS-Ortung" in html and tour.people_label in html and "📍 vor Ort" in html
    data = json.loads(html.split('<script id="where-data" type="application/json">')[1].split("</script>")[0])
    person = next(p for p in data["people"] if p["id"] == tour.pk)
    assert person["state"] == "at" and person["stops"] and "lat" in person

    part = c.get(reverse("planning:where"), {"datum": tour.date.isoformat(), "zeit": "05:00"},
                 headers={"HX-Request": "true", "HX-Target": "where-list"})
    assert part.content.decode().strip().startswith('<div id="where-list"') and "HX-Push-Url" in part.headers
    assert "noch nicht unterwegs" in part.content.decode()


def test_bad_input_and_empty_days(demo):
    c = client_for(roles.PROCESSING, "sb")
    assert c.get(reverse("planning:where"), {"datum": "kaputt", "zeit": "99:99", "art": "x"}).status_code == 200
    empty = c.get(reverse("planning:where"), {"datum": "2031-01-01"}).content.decode()
    assert "keine Fahrpläne" in empty


def test_kind_filter(demo):
    tour, stops = a_tour()
    kinds = {p.kind for p in whereabouts.positions(tour.date, 600, "installation")}
    assert kinds <= {"installation", "mixed"}


def test_behind_plan_hint_only_today(demo, monkeypatch):
    tour, stops = a_tour()
    late = 23 * 60
    assert all(not p.behind for p in whereabouts.positions(tour.date, late, today=tour.date + datetime.timedelta(days=1)))
    today = [p for p in whereabouts.positions(tour.date, late, today=tour.date) if p.tour.pk == tour.pk][0]
    assert today.behind == [s.n for s in today.stops if not s.reported]


def test_only_office_and_menu_link(demo):
    reader = client_for(roles.READER, "abl")
    assert reader.get(reverse("planning:where")).status_code == 403
    html = client_for(roles.DISPATCHER, "dispo").get(reverse("planning:calendar")).content.decode()
    assert "🛰 Wer ist wo?" in html and 'class="tab' not in html  # the pages are in the ☰ menu now
