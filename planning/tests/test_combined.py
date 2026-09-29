"""Readings and installations in one plan, and the calendar showing which is which."""

import datetime
import json

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from buildings.models import Building, InstallationOrder, OrderStatus
from conflicts.models import Conflict
from conflicts.services import acknowledge
from core import roles
from planning import services
from planning.calendar import calendar_events, tour_kind
from planning.models import Employee, StopKind, Tour

pytestmark = pytest.mark.django_db
DAY = datetime.date(2026, 10, 22)


@pytest.fixture
def demo(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: datetime.date(2026, 9, 28))
    monkeypatch.setattr(services, "get_client", lambda: None)


def login(role, name="u"):
    user = User.objects.create_user(username=name)
    user.groups.add(Group.objects.get(name=role))
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def dispo(demo):
    return login(roles.DISPATCHER, "dispo")


def free_building():
    return Building.objects.filter(tour_stops__isnull=True).order_by("file_number").first()


def free_order():
    return InstallationOrder.objects.filter(tour_stops__isnull=True).exclude(status=OrderStatus.DONE).order_by("re_number").first()


def all_rounder():
    """Somebody who is reader AND installer and has no plan on DAY."""
    return Employee.objects.filter(can_read=True, can_install=True).exclude(tours__date=DAY).first()


# --- one plan with both --------------------------------------------------------------

def test_both_selections_go_into_one_dialog_and_plan(dispo):
    building, order = free_building(), free_order()
    dispo.post(reverse("planning:select"), {"building": building.pk, "checked": "on"})
    bar = dispo.post(reverse("orders:select"), {"order": order.pk, "checked": "on"}).content.decode()
    assert "+ 📖 1" in bar  # the Montage bar tells that a building is ticked, too
    dialog = dispo.get(reverse("planning:montage_dialog")).content.decode()
    assert "Fahrplan mit Ablesung und Montage" in dialog and "Ableser + Monteur" in dialog

    person = all_rounder()
    dispo.post(reverse("planning:dialog"), {"employee": person.pk, "date": DAY.isoformat(), "start": "08:00",
                                            "break_minutes": 30, "strategy": "far"})
    preview = dispo.get(reverse("planning:draft")).content.decode()
    assert preview.count("art-tag reading") == 1 and preview.count("art-tag installation") == 1
    dispo.post(reverse("planning:draft_save"), {"confirm": "0", "sure": "1"})  # "final erstellen" in the question "Bist du sicher?"
    tour = Tour.objects.get(employee=person, date=DAY)
    assert {s.kind for s in tour.stops.all()} == {StopKind.READING, StopKind.INSTALLATION}
    assert tour_kind(list(tour.stops.all())) == "mixed"
    assert services.get_selection(dispo.session) == services.get_order_selection(dispo.session) == []


def test_reader_only_person_gets_a_warning_for_an_installation(demo):
    reader = Employee.objects.filter(can_read=True, can_install=False).first()
    draft = services.create_draft([free_building().pk], reader, DAY, datetime.time(8), 30, "far", order_ids=[free_order().pk])
    preview = services.calculate_preview(draft)
    installation = next(s for s in preview.stops if s.kind == StopKind.INSTALLATION)
    assert any("nicht als Monteur eingetragen" in f.message for f in installation.findings)


def test_add_a_stop_by_search_and_refuse_duplicates(dispo):
    building = free_building()
    dispo.post(reverse("planning:select"), {"building": building.pk, "checked": "on"})
    dispo.post(reverse("planning:dialog"), {"employee": all_rounder().pk, "date": DAY.isoformat(), "start": "08:00",
                                            "break_minutes": 30, "strategy": "far"})
    order = free_order()
    found = dispo.get(reverse("planning:draft_search"), {"q": order.re_number}).content.decode()
    assert order.re_number in found and '"kind": "installation"' in found
    assert building.file_number not in dispo.get(reverse("planning:draft_search"), {"q": building.file_number}).content.decode()

    response = dispo.post(reverse("planning:draft_action"), {"action": "add", "kind": "installation", "pk": order.pk})
    assert f"Montage {order.re_number}" in response.content.decode() and "hinzugefügt" in response.content.decode()
    again = dispo.post(reverse("planning:draft_action"), {"action": "add", "kind": "installation", "pk": order.pk})
    assert "schon im Plan" in again.content.decode()
    assert len(dispo.session[services.DRAFT_KEY]["stops"]) == 2


def test_suggestion_open_order_of_a_building_in_the_plan(demo):
    order = InstallationOrder.objects.filter(building__isnull=False, tour_stops__isnull=True).exclude(status=OrderStatus.DONE).first()
    draft = services.create_draft([order.building.pk], all_rounder(), DAY, datetime.time(8), 30, "far")
    suggestions, _ = services.draft_suggestions(draft)
    assert any(s.kind == StopKind.INSTALLATION and s.pk == order.pk for s in suggestions)


def test_suggestion_reading_for_an_installation_building(demo):
    order = next(o for o in InstallationOrder.objects.filter(building__isnull=False).exclude(status=OrderStatus.DONE)
                 if not o.building.tour_stops.filter(kind=StopKind.READING).exists())
    draft = services.create_draft([], all_rounder(), DAY, datetime.time(8), 30, "far", order_ids=[order.pk])
    assert any(s.kind == StopKind.READING and s.pk == order.building_id for s in services.draft_suggestions(draft)[0])


# --- calendar --------------------------------------------------------------------------

def events(start=datetime.date(2026, 9, 1), end=datetime.date(2027, 3, 1), kind=""):
    return [e for e in calendar_events(start, end, Employee.objects.all(), True, kind) if e.get("id")]


def test_events_say_what_kind_of_plan_they_are(demo):
    kinds = {e["extendedProps"]["kind"] for e in events()}
    assert {"reading", "installation"} <= kinds
    for event in events():
        assert f"kind-{event['extendedProps']['kind']}" in event["classNames"]
        assert any(icon in event["title"] for icon in ("📖", "🔧"))
        assert event["extendedProps"]["tooltip"].count("\n") >= 1  # one line per stop
    montage = events(kind="installation")
    assert montage and all(e["extendedProps"]["kind"] == "installation" for e in montage)
    assert all("🔧" in e["title"] and "Montage" in e["title"] for e in montage)


def test_feed_filter_by_kind(dispo):
    url = reverse("planning:calendar_feed")
    all_tours = [e for e in json.loads(dispo.get(url, {"start": "2026-09-01", "end": "2027-03-01"}).content) if e.get("id")]
    readings = [e for e in json.loads(dispo.get(url, {"start": "2026-09-01", "end": "2027-03-01", "art": "reading"}).content) if e.get("id")]
    assert 0 < len(readings) < len(all_tours)


def test_accepted_conflict_no_longer_marks_the_calendar(demo):
    conflict = Conflict.objects.filter(severity="critical", stop__isnull=False).select_related("stop__tour").first()
    tour_id = conflict.stop.tour_id
    assert "⚠" in next(e for e in events() if e["id"] == tour_id)["title"]
    for c in Conflict.objects.filter(stop__tour_id=tour_id) | Conflict.objects.filter(other_stop__tour_id=tour_id):
        if c.severity in ("critical", "warning"):
            acknowledge(c, User.objects.create_user(username=f"d{c.pk}", is_superuser=True), "abgesprochen")
    assert "⚠" not in next(e for e in events() if e["id"] == tour_id)["title"]


def test_person_overview(dispo):
    kaiser = Employee.objects.get(short_name="Kaiser")
    html = dispo.get(reverse("planning:person", args=[kaiser.pk])).content.decode()
    assert "Kaiser" in html and "🔧 Monteur" in html and "Nächste Fahrpläne" in html
    assert "Erster freier Tag" in html
    assert "KW" not in html  # weekly hours: only Admin (test_team_and_free_days.py)
    plans = Tour.objects.filter(employee=kaiser, date__gte=datetime.date(2026, 9, 28)).count()
    assert html.count('hx-get="/planung/fahrplan/') == min(12, plans)


def test_reader_sees_only_their_own_overview(demo):
    reader_user = User.objects.create_user(username="abl")
    reader_user.groups.add(Group.objects.get(name=roles.READER))
    own = Employee.objects.create(user=reader_user, short_name="Abl")
    client = Client()
    client.force_login(reader_user)
    assert client.get(reverse("planning:person", args=[own.pk])).status_code == 200
    other = Employee.objects.get(short_name="Kaiser")
    assert client.get(reverse("planning:person", args=[other.pk])).status_code == 404


def test_day_panel_shows_planned_and_free_people_and_prefills_the_dialog(dispo):
    tour = Tour.objects.order_by("date").first()
    html = dispo.get(reverse("planning:day"), {"datum": tour.date.isoformat()}).content.decode()
    assert str(tour.employee) in html and "Noch frei" in html
    # a montage order is ticked: only installers get a "planen" button
    dispo.post(reverse("orders:select"), {"order": free_order().pk, "checked": "on"})
    html = dispo.get(reverse("planning:day"), {"datum": DAY.isoformat()}).content.decode()
    assert "planen (1)" in html and "nur Ablesung" in html
    person = all_rounder()
    dialog = dispo.get(reverse("planning:dialog"), {"datum": DAY.isoformat(), "person": person.pk}).content.decode()
    assert f'value="{DAY.isoformat()}"' in dialog and f'<option value="{person.pk}" selected>' in dialog


def test_day_panel_is_for_the_office_only(demo):
    reader = login(roles.READER, "abl2")
    assert reader.get(reverse("planning:day"), {"datum": DAY.isoformat()}).status_code == 403


def test_suggestions_keep_to_7_5_hours_because_the_system_chooses_them(demo):
    order = InstallationOrder.objects.filter(building__isnull=False, tour_stops__isnull=True).exclude(status=OrderStatus.DONE).first()
    draft = services.create_draft([order.building.pk], all_rounder(), DAY, datetime.time(8), 30, "far")
    fitting, left_out = services.draft_suggestions(draft, net_minutes=0)
    assert any(s.pk == order.pk for s in fitting) and left_out == 0
    fitting, left_out = services.draft_suggestions(draft, net_minutes=445)  # the day is (almost) full
    assert fitting == [] and left_out >= 1
