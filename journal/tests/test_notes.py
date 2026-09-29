"""📝 Notes from the Terminierung: write, add more on top, erledigt, ⛔ Storno at planning."""

import datetime

import pytest
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from buildings.models import Building, InstallationOrder
from core import roles
from journal import notes
from journal.models import Note, NoteKind
from planning import services
from planning.models import Employee, StopKind, Tour

pytestmark = pytest.mark.django_db
DAY = datetime.date(2026, 11, 10)


@pytest.fixture
def demo(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: datetime.date(2026, 9, 28))
    monkeypatch.setattr(services, "get_client", lambda: None)


def user(role, name):
    u = User.objects.create_user(username=name)
    u.groups.add(Group.objects.get(name=role))
    return u


def client_for(u):
    c = Client()
    c.force_login(u)
    return c


def unplanned_building():
    return Building.objects.filter(tour_stops__isnull=True).order_by("file_number").first()


def test_terminierung_writes_and_planning_adds_more_on_top(demo):
    building = unplanned_building()
    sb = client_for(user(roles.PROCESSING, "termin"))
    response = sb.post(reverse("journal:note_add", args=["liegenschaft", building.pk]),
                       {"kind": "storno", "text": "Mieter hat abgesagt"})
    assert "Storno gespeichert" in response.content.decode() and response["HX-Trigger"] == "notes-changed"
    dispo = client_for(user(roles.DISPATCHER, "dispo"))
    dispo.post(reverse("journal:note_add", args=["liegenschaft", building.pk]), {"kind": "info", "text": "Neuer Termin ab November"})
    box = dispo.get(reverse("journal:notes", args=["liegenschaft", building.pk])).content.decode()
    box = box.split('class="note-list"')[1]  # the notes (the input field has an example text)
    assert box.index("Neuer Termin ab November") < box.index("Mieter hat abgesagt")  # the newest on top
    assert "termin" in box and "dispo" in box


def test_erledigt_and_permissions(demo):
    order = InstallationOrder.objects.first()
    termin = user(roles.PROCESSING, "termin")
    note = notes.add_note(termin, "bitte vorher anrufen", NoteKind.WISH, order=order)
    client_for(termin).post(reverse("journal:note_resolve", args=[note.pk]), {"done": "1"})
    note.refresh_from_db()
    assert note.resolved_at and note.resolved_by == termin
    chef = user(roles.MANAGEMENT, "chef")  # may read, not write
    with pytest.raises(PermissionDenied):
        notes.add_note(chef, "x", order=order)
    assert client_for(chef).get(reverse("journal:notes", args=["auftrag", order.pk])).status_code == 200
    assert client_for(user(roles.READER, "abl")).get(reverse("journal:notes", args=["auftrag", order.pk])).status_code == 403
    with pytest.raises(Exception):
        notes.add_note(termin, "   ", order=order)  # empty text


def test_storno_is_never_suggested_but_found_and_marked(demo):
    employee = Employee.objects.filter(can_read=True, active=True).first()
    found, _ = services.free_day_suggestions(employee, DAY, "reading")
    building = Building.objects.get(pk=found[0].pk)
    notes.add_note(user(roles.PROCESSING, "termin"), "storniert", NoteKind.STORNO, building=building)
    again, _ = services.free_day_suggestions(employee, DAY, "reading")
    assert building.pk not in [s.pk for s in again]
    draft = services.create_draft([], employee, DAY, datetime.time(8), 30, "short")
    results = services.search_targets(building.file_number, draft)
    assert any(r.pk == building.pk and "⛔ Storno gemeldet" in r.reason for r in results)


def test_storno_shows_in_the_plan_and_in_the_question(demo):
    building = unplanned_building()
    employee = Employee.objects.filter(can_read=True, active=True).first()
    other = Building.objects.filter(tour_stops__isnull=True).exclude(pk=building.pk).first()
    notes.add_note(user(roles.PROCESSING, "termin"), "Eigentümer storniert", NoteKind.STORNO, building=building)
    dispo = client_for(user(roles.DISPATCHER, "dispo"))
    session = dispo.session
    session[services.DRAFT_KEY] = services.create_draft([building.pk, other.pk], employee, DAY, datetime.time(8), 30, "short")
    session.save()
    page = dispo.get(reverse("planning:draft")).content.decode()
    assert "Eigentümer storniert" in page and "pr-stopp reading storno" in page
    question = dispo.get(reverse("planning:plan_confirm")).content.decode()
    assert "Storno gemeldet" in question and "aus dem Plan nehmen" in question


def test_badges_in_the_lists(demo):
    building = unplanned_building()
    termin = user(roles.PROCESSING, "termin")
    notes.add_note(termin, "Schlüssel beim Hausmeister", NoteKind.ACCESS, building=building)
    page = client_for(termin).get(reverse("buildings:list"), {"q": building.file_number}).content.decode()
    assert "📝 1" in page
    order = InstallationOrder.objects.first()
    notes.add_note(termin, "Storno", NoteKind.STORNO, order=order)
    dispo = client_for(user(roles.DISPATCHER, "dispo"))
    assert "⛔ Storno" in dispo.get(reverse("orders:list"), {"q": order.re_number}).content.decode()


def test_calendar_panel_shows_notes(demo):
    tour = Tour.objects.filter(stops__kind=StopKind.READING).first()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    notes.add_note(user(roles.PROCESSING, "termin"), "Hund im Hof", NoteKind.INFO, building=stop.building)
    html = client_for(user(roles.DISPATCHER, "dispo")).get(reverse("planning:tour_detail", args=[tour.pk])).content.decode()
    assert "Hund im Hof" in html and Note.objects.count() == 1
