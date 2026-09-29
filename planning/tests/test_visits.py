"""What happens after the visit: Ergebnis, Nachtermin, 1./2./3. Termin, 🧾 Bearbeitung per object."""

import datetime

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from buildings.models import Building
from core import roles
from planning import services, visits
from planning.models import Employee, StopKind, Tour, TourStop, Visit

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2026, 9, 28)


@pytest.fixture
def demo(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: TODAY)
    monkeypatch.setattr(services, "get_client", lambda: None)


def user(role, name):
    u = User.objects.create_user(username=name)
    u.groups.add(Group.objects.get(name=role))
    return u


def client_for(u):
    c = Client()
    c.force_login(u)
    return c


def reader_tour():
    """A plan with readings; its person gets a login as Ableser."""
    tour = Tour.objects.filter(stops__kind=StopKind.READING).order_by("date").first()
    reader = user(roles.READER, f"abl{tour.pk}")
    tour.employee.user = reader
    tour.employee.save()
    return tour, reader


def free_day(employee, after):
    day = after + datetime.timedelta(days=1)
    while Tour.objects.filter(employee=employee, date=day).exists() or day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    return day


def test_partial_needs_a_text_and_makes_a_nachtermin(demo):
    tour, reader = reader_tour()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    phone = client_for(reader)
    empty = phone.post(reverse("planning:stop_report", args=[stop.pk]), {"outcome": "partial", "todo": " "})
    assert "Bitte eintragen, was noch zu tun ist" in empty.content.decode() and empty["HX-Reswap"] == "none"
    phone.post(reverse("planning:stop_report", args=[stop.pk]), {"outcome": "partial", "todo": "NE003 und NE007 fehlen"})
    stop.refresh_from_db()
    visit = Visit.objects.get(stop=stop)
    assert stop.outcome == "partial" and stop.done_at and visit.attempt == 1 and visit.todo == "NE003 und NE007 fehlen"
    assert stop.building_id in visits.revisit_ids()[0]

    office = client_for(user(roles.PROCESSING, "sb"))
    listing = office.get(reverse("buildings:list"), {"nachtermin": "noetig"}).content.decode()
    assert stop.building.street in listing and "Nachtermin nötig" in listing
    box = office.get(reverse("planning:visits", args=["liegenschaft", stop.building_id])).content.decode()
    assert "1. Termin" in box and "◐ teilweise erledigt" in box and "NE003 und NE007 fehlen" in box and "2. Termin" in box


def test_absent_needs_a_reason(demo):
    tour, reader = reader_tour()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    html = client_for(reader).post(reverse("planning:stop_report", args=[stop.pk]),
                                   {"outcome": "absent", "todo": "alles", "reason": ""}).content.decode()
    assert "Bitte einen Grund wählen" in html
    client_for(reader).post(reverse("planning:stop_report", args=[stop.pk]),
                            {"outcome": "absent", "todo": "Mieter anrufen", "reason": "absent"})
    assert Visit.objects.get(stop=stop).reason == "absent"


def test_plan_again_as_second_visit_and_the_first_stays(demo):
    tour, reader = reader_tour()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    building = stop.building
    client_for(reader).post(reverse("planning:stop_report", args=[stop.pk]), {"outcome": "partial", "todo": "Keller fehlt"})

    # the system suggests it again for a free day
    found, _ = services.free_day_suggestions(tour.employee, free_day(tour.employee, tour.date), "reading")
    assert building.pk in [s.pk for s in found]

    # plan it again: the first visit stays in its plan, the preview says "2. Termin"
    day = free_day(tour.employee, tour.date)
    dispo = client_for(user(roles.DISPATCHER, "dispo"))
    session = dispo.session
    session[services.DRAFT_KEY] = services.create_draft([building.pk], tour.employee, day, datetime.time(8), 30, "short")
    session.save()
    page = dispo.get(reverse("planning:draft")).content.decode()
    assert "2. Termin (Nachtermin)" in page and "noch zu tun: Keller fehlt" in page
    services.save_draft(session[services.DRAFT_KEY], None, confirm=False)
    assert TourStop.objects.filter(pk=stop.pk).exists()  # the visited stop is history, not moved away
    assert building.pk not in visits.revisit_ids()[0]  # planned again

    new_stop = TourStop.objects.get(tour__date=day, tour__employee=tour.employee, building=building)
    new_tour = new_stop.tour
    new_tour.employee.user = reader
    client_for(reader).post(reverse("planning:stop_done", args=[new_stop.pk]), {"done": "1"})
    second = Visit.objects.get(stop=new_stop)
    assert second.attempt == 2 and second.outcome == "complete"
    assert building.pk not in visits.revisit_ids()[0]


def test_office_can_close_it_without_a_nachtermin(demo):
    tour, reader = reader_tour()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    client_for(reader).post(reverse("planning:stop_report", args=[stop.pk]),
                            {"outcome": "absent", "todo": "klärt die HV", "reason": "other"})
    visit = Visit.objects.get(stop=stop)
    dispo = client_for(user(roles.DISPATCHER, "dispo"))
    html = dispo.post(reverse("planning:visit_close", args=[visit.pk]), {"closed": "1"}).content.decode()
    assert "abgeschlossen" in html and stop.building_id not in visits.revisit_ids()[0]


def test_re_saving_a_plan_keeps_the_ergebnis(demo):
    tour, reader = reader_tour()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    client_for(reader).post(reverse("planning:stop_report", args=[stop.pk]), {"outcome": "partial", "todo": "NE001"})
    again = services.save_draft(services.draft_from_tour(Tour.objects.get(pk=tour.pk)), None, confirm=False)
    kept = again.stops.get(building=stop.building, kind=StopKind.READING)
    assert kept.outcome == "partial" and Visit.objects.get(building=stop.building).stop_id == kept.pk


def test_undo_removes_the_visit(demo):
    tour, reader = reader_tour()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    phone = client_for(reader)
    phone.post(reverse("planning:stop_done", args=[stop.pk]), {"done": "1"})
    assert Visit.objects.filter(stop=stop, outcome="complete").exists()
    phone.post(reverse("planning:stop_done", args=[stop.pk]), {"done": "0"})
    stop.refresh_from_db()
    assert not Visit.objects.filter(stop=stop).exists() and stop.outcome == "" and stop.done_at is None


def test_panel_shows_ergebnis_and_attempt(demo):
    tour, reader = reader_tour()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    client_for(reader).post(reverse("planning:stop_report", args=[stop.pk]), {"outcome": "partial", "todo": "2 HKV fehlen"})
    html = client_for(user(roles.DISPATCHER, "dispo")).get(reverse("planning:tour_detail", args=[tour.pk])).content.decode()
    assert "◐ teilweise erledigt" in html and "noch zu tun: <b>2 HKV fehlen</b>" in html


def test_day_page_offers_the_reasons(demo):
    tour, reader = reader_tour()
    html = client_for(reader).get(reverse("planning:my_day"), {"datum": tour.date.isoformat()}).content.decode()
    assert '<option value="no_access">' in html and "◐ teilweise erledigt" in html and "✓ fertig (100 %)" in html
