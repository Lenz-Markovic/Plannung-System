"""🧾 Rückmeldungen: the office worklist after the visits (page, buttons, badge, roles)."""

import datetime

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from buildings.models import Building
from core import roles
from journal.models import Activity, Note, NoteKind
from planning import followup, services
from planning.models import StopKind, Tour, TourStop, Visit
from planning.rules.followup import CHECK, DONE, MISSING, PLANNED, REVISIT

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


def past_tour(days_ago=2):
    """A reading plan moved to a past day (with no other plan of that person that day); its person is an Ableser."""
    tour = Tour.objects.filter(stops__kind=StopKind.READING).exclude(stops__done_at__isnull=False).order_by("date").first()
    day = TODAY - datetime.timedelta(days=days_ago)
    Tour.objects.filter(employee=tour.employee, date=day).exclude(pk=tour.pk).delete()
    Tour.objects.filter(pk=tour.pk).update(date=day)
    tour.refresh_from_db()
    reader = user(roles.READER, f"abl{tour.pk}")
    tour.employee.user = reader
    tour.employee.save()
    return tour, reader


def first_stop(tour):
    return tour.stops.filter(kind=StopKind.READING).order_by("position").first()


def report(stop, reader, outcome, todo="", reason="", note=None):
    data = {"outcome": outcome, "todo": todo, "reason": reason}
    if note is not None:
        data["field_note"] = note
    return client_for(reader).post(reverse("planning:stop_report", args=[stop.pk]), data)


def entry_of(key, u):
    return followup.entry(key, u, TODAY)


def act(u, key, **data):
    return client_for(u).post(reverse("planning:followup_action", args=[key]), data)


def test_page_lists_reports_by_day_and_person(demo):
    tour, reader = past_tour()
    stop = first_stop(tour)
    report(stop, reader, "partial", "NE003 fehlt")
    html = client_for(user(roles.PROCESSING, "sb")).get(reverse("planning:followup")).content.decode()
    assert "🧾 Rückmeldungen aus dem Außendienst" in html
    assert f'id="fu-day-{tour.date:%Y-%m-%d}"' in html and tour.people_label in html
    assert "NE003 fehlt" in html and "🔁 Nachtermin nötig" in html and "1. Termin" in html


def test_missing_report_is_listed_and_the_office_enters_it(demo):
    tour, reader = past_tour()
    stop = first_stop(tour)
    office = user(roles.PROCESSING, "sb")
    assert entry_of(f"s{stop.pk}", office).state == MISSING
    html = client_for(office).get(reverse("planning:followup"), {"f": "ohne"}).content.decode()
    assert "kein Ergebnis" in html and stop.building.street in html

    empty = client_for(office).post(reverse("planning:followup_report", args=[stop.pk]), {"outcome": "partial", "todo": ""})
    assert empty["HX-Reswap"] == "none"
    answer = client_for(office).post(reverse("planning:followup_report", args=[stop.pk]), {"outcome": "complete"})
    assert "im Büro nachgetragen" in answer.content.decode()
    visit = Visit.objects.get(stop=stop)
    assert visit.entered_by_office and visit.reported_by == office
    assert entry_of(f"s{stop.pk}", office) is None and entry_of(f"v{visit.pk}", office).state == CHECK
    assert Activity.objects.filter(text__startswith="📝 im Büro nachgetragen").exists()


def test_today_and_old_stops_are_not_missing(demo):
    tour, _ = past_tour(days_ago=0)
    assert entry_of(f"s{first_stop(tour).pk}", user(roles.ADMIN, "adm")) is None


def test_check_a_complete_reading(demo):
    tour, reader = past_tour()
    stop = first_stop(tour)
    report(stop, reader, "complete")
    visit = Visit.objects.get(stop=stop)
    office = user(roles.PROCESSING, "sb")
    assert entry_of(f"v{visit.pk}", office).primary in ("check", "release")
    html = act(office, f"v{visit.pk}", action="check", closed_note="").content.decode()
    assert "✓ geprüft" in html and "Geprüft" in html
    visit.refresh_from_db()
    assert visit.closed_by == office and entry_of(f"v{visit.pk}", office).state == DONE
    twice = act(office, f"v{visit.pk}", action="check")
    assert twice["HX-Reswap"] == "none" and "Schon erledigt" in twice.content.decode()
    act(office, f"v{visit.pk}", action="reopen")
    assert entry_of(f"v{visit.pk}", office).state == CHECK


def test_closing_a_revisit_needs_a_reason(demo):
    tour, reader = past_tour()
    stop = first_stop(tour)
    report(stop, reader, "absent", "Mieter anrufen", "absent")
    visit = Visit.objects.get(stop=stop)
    office = user(roles.PROCESSING, "sb")
    empty = act(office, f"v{visit.pk}", action="close", closed_note=" ")
    assert empty["HX-Reswap"] == "none" and "Bitte kurz eintragen" in empty.content.decode()
    act(office, f"v{visit.pk}", action="close", closed_note="telefonisch geklärt")
    visit.refresh_from_db()
    assert visit.closed_at and visit.closed_note == "telefonisch geklärt"
    assert entry_of(f"v{visit.pk}", office).state == DONE


def test_closed_result_is_kept_and_a_new_note_opens_it_again(demo):
    tour, reader = past_tour()
    stop = first_stop(tour)
    report(stop, reader, "complete")
    visit = Visit.objects.get(stop=stop)
    office = user(roles.PROCESSING, "sb")
    act(office, f"v{visit.pk}", action="check")
    phone = client_for(reader)
    undo = phone.post(reverse("planning:stop_done", args=[stop.pk]), {"done": "0"})
    assert "schon abgeschlossen" in undo.content.decode() and Visit.objects.filter(pk=visit.pk).exists()
    day = phone.get(reverse("planning:my_day"), {"datum": tour.date.isoformat()}).content.decode()
    assert "vom Büro bearbeitet" in day
    phone.post(reverse("planning:stop_note", args=[stop.pk]), {"field_note": "Zähler 4711 getauscht"})
    visit.refresh_from_db()
    assert visit.closed_at is None and visit.note == "Zähler 4711 getauscht"
    assert entry_of(f"v{visit.pk}", office).state == CHECK


def test_wish_goes_to_the_disposition_and_planning_moves_it(demo):
    tour, reader = past_tour()
    stop = first_stop(tour)
    report(stop, reader, "partial", "Keller fehlt")
    visit = Visit.objects.get(stop=stop)
    office = user(roles.PROCESSING, "sb")
    assert entry_of(f"v{visit.pk}", office).primary == "wish"
    act(office, f"v{visit.pk}", action="wish", wish="Mieterin ab 16 Uhr")
    assert Note.objects.filter(kind=NoteKind.WISH, building=stop.building, text="Mieterin ab 16 Uhr").exists()
    e = entry_of(f"v{visit.pk}", office)
    assert e.has_wish and e.primary == "" and "Disposition" in e.step

    dispo = user(roles.DISPATCHER, "dispo")
    assert entry_of(f"v{visit.pk}", dispo).primary == "plan"
    day = TODAY + datetime.timedelta(days=3)
    while day.weekday() >= 5 or Tour.objects.filter(employee=tour.employee, date=day).exists():
        day += datetime.timedelta(days=1)
    services.save_draft(services.create_draft([stop.building_id], tour.employee, day, datetime.time(8), 30, "short"),
                        None, confirm=False)
    e = entry_of(f"v{visit.pk}", dispo)
    assert e.state == PLANNED and e.planned_on == day and e.planned_attempt == 2
    html = client_for(dispo).get(reverse("planning:followup"), {"f": "geplant"}).content.decode()
    assert "Nachtermin geplant" in html and "2. Termin" in html


def test_problem_from_the_phone_keeps_the_entry_open_until_resolved(demo):
    tour, reader = past_tour()
    stop = first_stop(tour)
    report(stop, reader, "complete")
    client_for(reader).post(reverse("planning:stop_note", args=[stop.pk]), {"field_note": "Wasserschaden", "problem": "1"})
    visit = Visit.objects.get(stop=stop)
    office = user(roles.PROCESSING, "sb")
    e = entry_of(f"v{visit.pk}", office)
    assert e.has_problem and e.primary == "resolve"
    act(office, f"v{visit.pk}", action="check")
    assert entry_of(f"v{visit.pk}", office).is_open  # still open: the problem
    note = Note.objects.get(kind=NoteKind.PROBLEM, building=stop.building)
    act(office, f"v{visit.pk}", action="resolve", note=note.pk, answer="HV informiert")
    note.refresh_from_db()
    assert note.resolved_at and Activity.objects.filter(text__contains="Wie gelöst: HV informiert").exists()
    assert not entry_of(f"v{visit.pk}", office).is_open


def test_release_from_the_list(demo):
    tour, reader = past_tour()
    stop = first_stop(tour)
    report(stop, reader, "complete")
    visit = Visit.objects.get(stop=stop)
    office = user(roles.PROCESSING, "sb")
    if not entry_of(f"v{visit.pk}", office).can_release:
        pytest.skip("demo building cannot be released without documents")
    act(office, f"v{visit.pk}", action="release")
    assert Building.objects.get(pk=stop.building_id).status == "released"
    assert entry_of(f"v{visit.pk}", office).state == DONE


def test_quiet_readings_are_checked_in_one_go(demo):
    tour, reader = past_tour()
    stops = list(tour.stops.filter(kind=StopKind.READING).order_by("position"))
    for stop in stops:
        report(stop, reader, "complete")
    office = user(roles.PROCESSING, "sb")
    quiet = [e for e in followup.decorate(followup.followup_entries(TODAY), office, TODAY) if e.quiet and e.date == tour.date]
    answer = client_for(office).post(reverse("planning:followup_quiet"), {"tag": tour.date.isoformat()})
    assert f"{len(quiet)} unauffällige abgehakt" in answer.content.decode()
    assert Visit.objects.filter(stop__in=stops, closed_at__isnull=False).count() == len(quiet)


def test_filters_counts_and_news(demo):
    tour, reader = past_tour()
    stops = list(tour.stops.filter(kind=StopKind.READING).order_by("position"))
    office = client_for(user(roles.PROCESSING, "sb"))
    report(stops[0], reader, "partial", "Keller fehlt")
    page = office.get(reverse("planning:followup"), {"f": "nachtermin"}).content.decode()
    assert "Keller fehlt" in page
    assert "Keller fehlt" not in office.get(reverse("planning:followup"), {"f": "pruefen"}).content.decode()
    stand = followup.stamp()
    same = office.get(reverse("planning:followup_counts"), {"stand": stand}).content.decode()
    if len(stops) > 1:
        report(stops[1], reader, "complete")
        news = office.get(reverse("planning:followup_counts"), {"stand": stand}).content.decode()
        assert news != same
    assert office.get(reverse("planning:followup"), {"q": "gibtesnicht-xyz"}).status_code == 200


def test_badge_counts_open_entries(demo):
    tour, reader = past_tour()
    office = client_for(user(roles.PROCESSING, "sb"))
    before = followup.open_count(TODAY)
    report(first_stop(tour), reader, "partial", "Keller fehlt")  # the ❓ becomes a 🔁: still one open
    assert followup.open_count(TODAY) == before
    html = office.get(reverse("planning:followup_badge")).content.decode()
    assert (f">{before}<" in html) if before else html.strip() == ""


def test_roles(demo):
    tour, reader = past_tour()
    stop = first_stop(tour)
    report(stop, reader, "absent", "Mieter anrufen", "absent")
    visit = Visit.objects.get(stop=stop)
    assert client_for(reader).get(reverse("planning:followup")).status_code == 403
    assert client_for(reader).get(reverse("planning:followup_badge")).content.decode() == ""

    lead = user(roles.MANAGEMENT, "leitung")
    page = client_for(lead).get(reverse("planning:followup")).content.decode()
    assert "Mieter anrufen" in page and "Abschließen" not in page and "nachtragen" not in page
    denied = act(lead, f"v{visit.pk}", action="close", closed_note="x")
    assert denied["HX-Reswap"] == "none" and Visit.objects.get(pk=visit.pk).closed_at is None
    assert client_for(lead).post(reverse("planning:followup_report", args=[stop.pk]),
                                 {"outcome": "complete"}).status_code == 403

    assert act(user(roles.ADMIN, "adm"), "x1", action="check").status_code == 404
    gone = act(user(roles.ADMIN, "adm2"), "v999999", action="check")
    assert "gibt es nicht mehr" in gone.content.decode()


def test_object_box_shows_close_picks_for_the_office(demo):
    tour, reader = past_tour()
    stop = first_stop(tour)
    report(stop, reader, "absent", "Mieter anrufen", "absent")
    box = client_for(user(roles.PROCESSING, "sb")).get(
        reverse("planning:visits", args=["liegenschaft", stop.building_id])).content.decode()
    assert "telefonisch geklärt" in box and "Mieter anrufen" in box
