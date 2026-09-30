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


# --- review fixes: visited stops are history ------------------------------------------------

def report_on(stop, reader, outcome, todo="x", reason="absent"):
    return client_for(reader).post(reverse("planning:stop_report", args=[stop.pk]),
                                   {"outcome": outcome, "todo": todo, "reason": reason})


def test_resaving_a_visited_plan_keeps_the_nachtermin_elsewhere(demo):
    tour, reader = reader_tour()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    building = stop.building
    report_on(stop, reader, "absent")
    day = free_day(tour.employee, tour.date)
    services.save_draft(services.create_draft([building.pk], tour.employee, day, datetime.time(8), 30, "short"),
                        None, confirm=False)
    nachtermin = TourStop.objects.get(tour__date=day, building=building)
    services.save_draft(services.draft_from_tour(Tour.objects.get(pk=tour.pk)), None, confirm=False)  # Neu rechnen
    assert TourStop.objects.filter(pk=nachtermin.pk).exists()  # still planned on the new day


def test_moving_a_visited_plan_moves_only_the_open_stops(demo):
    from django.db.models import Count
    tour = (Tour.objects.filter(stops__kind=StopKind.READING).exclude(stops__done_at__isnull=False)
            .annotate(n=Count("stops")).filter(n__gte=2).order_by("date").first())
    reader = user(roles.READER, "abl-move")
    tour.employee.user = reader
    tour.employee.save()
    stops = list(tour.stops.exclude(kind=StopKind.HELP).order_by("position"))
    assert len(stops) >= 2
    report_on(stops[0], reader, "partial", "NE003")
    day = free_day(tour.employee, tour.date)
    draft = services.draft_from_tour(Tour.objects.get(pk=tour.pk), date=day)
    assert draft["split_from"] == tour.pk and len(draft["stops"]) == len(stops) - 1
    new = services.save_draft(draft, None, confirm=False)
    old = Tour.objects.get(pk=tour.pk)
    assert list(old.stops.values_list("pk", flat=True)) == [stops[0].pk]  # the reported one stays as proof
    assert new.date == day and new.stops.count() == len(stops) - 1
    assert Visit.objects.get(stop=stops[0]).date == tour.date


def test_same_day_second_visit_counts_and_decides(demo):
    tour, reader = reader_tour()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    building = stop.building
    report_on(stop, reader, "absent")
    other = Employee.objects.filter(can_read=True, active=True).exclude(pk=tour.employee_id)\
        .exclude(tours__date=tour.date).first()
    services.save_draft(services.create_draft([building.pk], other, tour.date, datetime.time(15), 30, "short"),
                        None, confirm=False)
    second = TourStop.objects.get(tour__employee=other, tour__date=tour.date, building=building)
    other.user = user(roles.READER, "abl-second")
    other.save()
    client_for(other.user).post(reverse("planning:stop_done", args=[second.pk]), {"done": "1"})
    visit = Visit.objects.get(stop=second)
    assert visit.attempt == 2 and building.pk not in visits.revisit_ids()[0]  # the later report decides


def test_after_a_complete_visit_it_starts_again_at_1(demo):
    tour, reader = reader_tour()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    client_for(reader).post(reverse("planning:stop_done", args=[stop.pk]), {"done": "1"})
    day = free_day(tour.employee, tour.date)
    draft = services.create_draft([stop.building_id], tour.employee, day, datetime.time(8), 30, "short")
    preview = services.calculate_preview(draft)
    visits.attach_attempts(preview.stops, preview.date)
    again = next(s for s in preview.stops if s.building and s.building.pk == stop.building_id)
    assert again.attempt == 1 and again.last_visit is None  # not a "Nachtermin"


def test_closed_result_cannot_be_undone_from_the_phone(demo):
    tour, reader = reader_tour()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    report_on(stop, reader, "absent", "klärt die HV", "other")
    visit = Visit.objects.get(stop=stop)
    visits.close(visit, user(roles.ADMIN, "adm"), True)
    answer = client_for(reader).post(reverse("planning:stop_done", args=[stop.pk]), {"done": "0"})
    assert "schon abgeschlossen" in answer.content.decode() and Visit.objects.filter(pk=visit.pk).exists()


def test_help_stop_only_reports_fertig(demo):
    tour, reader = reader_tour()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    stop.kind, stop.help_tour = StopKind.HELP, tour
    stop.save()
    html = report_on(stop, reader, "partial", "3 HKV fehlen").content.decode()
    assert "Bei einer Hilfe meldet der Plan" in html and not Visit.objects.filter(stop=stop).exists()


def test_note_typed_before_the_button_is_saved_with_it(demo):
    tour, reader = reader_tour()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    client_for(reader).post(reverse("planning:stop_done", args=[stop.pk]), {"done": "1", "field_note": "Zähler neu"})
    stop.refresh_from_db()
    assert stop.field_note == "Zähler neu" and Visit.objects.get(stop=stop).note == "Zähler neu"
