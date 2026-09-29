"""Calendar: events, drag & drop (-> draft -> confirm), permissions."""

import datetime

import pytest
from django.contrib.auth.models import Group, User
from django.urls import reverse
from django.utils import timezone

from core import roles
from planning import services
from planning.models import Absence, Employee, Tour
from planning.tests.test_planning_flow import FakeTomTom

pytestmark = pytest.mark.django_db
FREE_DAY = datetime.date(2027, 3, 2)  # no tours in the demo data


@pytest.fixture
def demo(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: datetime.date(2026, 9, 28))
    monkeypatch.setattr(services, "get_client", lambda: FakeTomTom())


def login(client, role, username="u"):
    user = User.objects.create_user(username=username, password="x")
    user.groups.add(Group.objects.get(name=role))
    client.force_login(user)
    return user


def feed(client, **params):
    params.setdefault("start", "2026-12-01")
    params.setdefault("end", "2027-01-01")
    return client.get(reverse("planning:calendar_feed"), params).json()


def test_feed_has_one_event_per_tour_in_range(client, demo):
    login(client, roles.DISPATCHER)
    events = [e for e in feed(client) if "tour" in e.get("classNames", [])]
    tours = Tour.objects.filter(date__gte="2026-12-01", date__lt="2027-01-01")
    assert len(events) == tours.count() > 0
    event = next(e for e in events if e["id"] == tours.first().pk)
    assert event["backgroundColor"] == tours.first().employee.calendar_color
    assert event["title"].startswith("⏳")  # imported tours are provisional


def test_installation_conflict_is_marked(client, demo):
    """0798792: reading 03.12. (Hofmann), installation 07.12. -> ⚠ on Hofmann's tour."""
    login(client, roles.DISPATCHER)
    hofmann = Employee.objects.get(short_name="Hofmann")
    events = feed(client, person=hofmann.pk)
    tour = Tour.objects.get(employee=hofmann, date="2026-12-03")
    assert "⚠" in next(e for e in events if e.get("id") == tour.pk)["title"]


def test_drag_and_drop_makes_a_draft_and_only_confirm_moves_the_tour(client, demo):
    login(client, roles.DISPATCHER)
    tour = Tour.objects.filter(stops__kind="reading").first()
    old_date = tour.date
    response = client.post(reverse("planning:tour_move", args=[tour.pk]), {"date": FREE_DAY.isoformat(), "version": tour.version})
    assert response.json() == {"redirect": reverse("planning:draft")}
    tour.refresh_from_db()
    assert tour.date == old_date  # nothing changed yet
    page = client.get(reverse("planning:draft")).content.decode()
    assert "Verschiebung: bisher" in page
    response = client.post(reverse("planning:draft_save"), {"confirm": "1", "sure": "1"})  # "final erstellen" in the question "Bist du sicher?"
    tour.refresh_from_db()
    # back to the calendar on the new day, and the Excel file of the tour is downloaded
    assert response["Location"] == f"{reverse('planning:calendar')}?datum={FREE_DAY.isoformat()}&excel={tour.pk}"
    assert (tour.date, tour.status) == (FREE_DAY, "confirmed")
    assert tour.stops.filter(kind="reading").exists()  # stops moved with the tour, not deleted


def test_cannot_move_onto_a_day_that_already_has_a_tour(client, demo):
    login(client, roles.DISPATCHER)
    employee = Employee.objects.filter(tours__isnull=False).first()
    first, second = employee.tours.order_by("date")[:2]
    response = client.post(reverse("planning:tour_move", args=[first.pk]), {"date": second.date.isoformat()})
    assert response.status_code == 400 and "schon einen Fahrplan" in response.json()["message"]


def test_outdated_version_is_refused(client, demo):
    login(client, roles.DISPATCHER)
    tour = Tour.objects.first()
    response = client.post(reverse("planning:tour_move", args=[tour.pk]), {"date": FREE_DAY.isoformat(), "version": tour.version - 1})
    assert response.status_code == 409


def test_absence_is_shown(client, demo):
    login(client, roles.DISPATCHER)
    keller = Employee.objects.get(short_name="Keller")
    Absence.objects.create(employee=keller, start_date=datetime.date(2026, 12, 21), end_date=datetime.date(2026, 12, 24))
    labels = [e["title"] for e in feed(client, person=keller.pk) if "absence-label" in e.get("classNames", [])]
    assert labels == ["🏖 Keller: Urlaub"]


def test_reader_sees_only_own_tours_and_cannot_move(client, demo):
    user = login(client, roles.READER, "abl")
    keller = Employee.objects.get(short_name="Keller")
    keller.user = user
    keller.save()
    events = [e for e in feed(client, start="2026-09-01", end="2027-06-01") if "tour" in e.get("classNames", [])]
    assert events and {Tour.objects.get(pk=e["id"]).employee for e in events} == {keller}
    assert not events[0]["editable"]
    assert client.post(reverse("planning:tour_move", args=[events[0]["id"]]), {"date": FREE_DAY.isoformat()}).status_code == 403


def test_delete_tour(client, demo):
    login(client, roles.DISPATCHER)
    tour = Tour.objects.first()
    response = client.post(reverse("planning:tour_delete", args=[tour.pk]))
    assert response["HX-Trigger"] == "calendar-changed"
    assert not Tour.objects.filter(pk=tour.pk).exists()


def test_calendar_page(client, demo):
    login(client, roles.MANAGEMENT)
    html = client.get(reverse("planning:calendar")).content.decode()
    assert 'data-editable="0"' in html and "fullcalendar" in html


def test_side_panel_with_reading_and_installation_stops(client, demo):
    """Regression: the panel crashed for stops without an installation order."""
    login(client, roles.DISPATCHER)
    tour = Tour.objects.filter(stops__kind="installation").filter(stops__kind="reading").first() or Tour.objects.first()
    response = client.get(reverse("planning:tour_detail", args=[tour.pk]))
    assert response.status_code == 200 and "Neu rechnen" in response.content.decode()
