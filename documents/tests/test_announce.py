"""📄 Aushänge & Ankündigungen: how tenants are told, Aushang-Fahrten planned like a reading, ✓ aufgehängt."""

import datetime

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core import roles
from documents import notice_rules as rules
from documents import notices
from journal.models import Activity
from planning import services, visits
from planning.models import Employee, StopKind, Tour, TourStop, Visit

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2026, 9, 28)


@pytest.fixture
def demo(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: TODAY)
    monkeypatch.setattr(services, "get_client", lambda: None)


def login(role, name):
    user = User.objects.create_user(username=name)
    user.groups.add(Group.objects.get(name=role))
    client = Client()
    client.force_login(user)
    return client, user


def appointment():
    """A coming reading appointment (the demo plans have no saved times: the time is estimated)."""
    return (TourStop.objects.filter(kind=StopKind.READING, tour__date__gt=TODAY + datetime.timedelta(days=14),
                                    done_at__isnull=True, outcome="")
            .select_related("tour", "building").order_by("tour__date", "position").first())


def save(client, stop, **data):
    return client.post(reverse("documents:announce_save", args=[stop.pk]), data)


def test_page_lists_coming_appointments_with_a_time(demo):
    client, _ = login(roles.DISPATCHER, "dispo")
    stop = appointment()
    html = client.get(reverse("documents:announce"), {"zeitraum": "alle"}).content.decode()
    assert "📄 Aushänge &amp; Ankündigungen" in html and stop.building.file_number in html
    assert "❔ Ankündigung offen" in html and "Uhr" in html and "geschätzt" in html  # imported plan: estimated time
    row = notices.row_of(stop, TODAY)
    assert row.window and row.window_source == "estimate" and row.state == rules.A_OPEN


def test_channel_units_and_time_by_hand_go_on_the_notice(demo):
    client, _ = login(roles.DISPATCHER, "dispo")
    stop = appointment()
    answer = save(client, stop, channel=rules.AUSHANG, scope=rules.SOME_UNITS, units="Whg 3 (Müller), Whg 7",
                  von="8", bis="12:00").content.decode()
    assert "Gespeichert" in answer and "von Hand" in answer and "08:00–12:00 Uhr" in answer
    stop.refresh_from_db()
    assert stop.notice_channel == rules.AUSHANG and stop.notice_wanted and stop.notice_from == datetime.time(8)
    fields = notices.fields_of(stop)
    assert fields.time == "08:00 – 12:00 Uhr" and fields.bottom == "Nur für: Whg 3 (Müller), Whg 7"
    page = client.get(reverse("documents:notice_page"), {"stop": stop.pk}).content.decode()
    assert "Nur für: Whg 3 (Müller), Whg 7" in page and "08:00 – 12:00 Uhr" in page
    assert Activity.objects.filter(text__contains="Ankündigung: 📄 Aushang durch uns").exists()
    bad = save(client, stop, von="25:99")
    assert bad["HX-Reswap"] == "none" and "Zeit bitte" in bad.content.decode()
    save(client, stop, von="", bis="")                                               # back to the plan's time
    stop.refresh_from_db()
    assert stop.notice_from is None and notices.window_of(stop)[1] == "estimate"


def test_mail_to_the_property_manager_is_marked_sent(demo):
    client, _ = login(roles.PROCESSING, "sb")
    stop = appointment()
    save(client, stop, channel=rules.MAIL_HV)
    stop.refresh_from_db()
    assert notices.row_of(stop, TODAY).state == rules.A_SEND
    html = client.post(reverse("documents:announce_sent", args=[stop.pk]), {"done": "1"}).content.decode()
    assert "Mail verschickt" in html
    stop.refresh_from_db()
    assert stop.notice_sent_at and notices.row_of(stop, TODAY).state == rules.A_DONE
    client.post(reverse("documents:announce_sent", args=[stop.pk]), {"done": "0"})
    stop.refresh_from_db()
    assert stop.notice_sent_at is None


def plan_trip(client, stop, person, day):
    client.post(reverse("documents:notice_select"), {"building": stop.building_id, "order": "", "checked": "on"})
    dialog = client.get(reverse("planning:dialog")).content.decode()
    assert "📄 Aushang-Fahrt planen" in dialog and stop.building.file_number in dialog
    response = client.post(reverse("planning:dialog"), {"employee": person.pk, "date": day.isoformat(), "start": "08:00",
                                                        "break_minutes": "30", "strategy": "far"})
    assert response["HX-Redirect"] == reverse("planning:draft")
    return services.save_draft(client.session[services.DRAFT_KEY], None, confirm=False)


def free_day(person, after):
    day = after
    while day.weekday() >= 5 or Tour.objects.filter(employee=person, date=day).exists():
        day += datetime.timedelta(days=1)
    return day


def test_aushang_trip_is_planned_like_a_reading_and_reported_on_the_phone(demo):
    client, _ = login(roles.DISPATCHER, "dispo")
    stop = appointment()
    save(client, stop, channel=rules.AUSHANG)
    TourStop.objects.filter(pk=stop.pk).update(notice_printed_at=timezone.now())
    stop.refresh_from_db()
    assert notices.row_of(stop, TODAY).state == rules.A_TRIP
    person = Employee.objects.filter(active=True).exclude(pk=stop.tour.employee_id).order_by("short_name").first()
    day = free_day(person, TODAY + datetime.timedelta(days=1))
    tour = plan_trip(client, stop, person, day)
    trip = tour.stops.get()
    assert trip.kind == StopKind.NOTICE and trip.building_id == stop.building_id and trip.work_minutes == rules.DEFAULT_TRIP_MINUTES
    row = notices.row_of(stop, TODAY)
    assert row.state == rules.A_TRIP_PLANNED and row.trip == trip
    assert TourStop.objects.filter(pk=stop.pk).exists()             # the reading appointment stays where it is
    assert notices.appointment_of(trip) == stop

    # the trip is not a Nachtermin and not a missing report
    assert stop.building_id not in visits.revisit_ids()[0]
    from planning import followup
    assert not [e for e in followup.followup_entries(day + datetime.timedelta(days=1)) if e.stop and e.stop.pk == trip.pk]

    # Mein Tag: the person sees what the notice is for and taps ✓ aufgehängt
    reader, user = login(roles.READER, "abl-trip")
    person.user = user
    person.save()
    page = reader.get(reverse("planning:my_day"), {"datum": day.isoformat()}).content.decode()
    assert "📄 Aushang aufhängen" in page and f"{stop.tour.date:%d.%m.%Y}" in page and "✓ aufgehängt" in page
    assert "teilweise erledigt" not in page
    reader.post(reverse("planning:stop_done", args=[trip.pk]), {"done": "1"})
    stop.refresh_from_db()
    assert stop.notice_sent_at and stop.notice_sent_by == user and not Visit.objects.filter(stop=trip).exists()
    assert notices.row_of(stop, TODAY).state == rules.A_DONE
    reader.post(reverse("planning:stop_done", args=[trip.pk]), {"done": "0"})       # a mistake: back
    stop.refresh_from_db()
    assert stop.notice_sent_at is None
    wrong = reader.post(reverse("planning:stop_report", args=[trip.pk]), {"outcome": "partial", "todo": "x"})
    assert "hängt oder nicht" in wrong.content.decode()


def test_resaving_the_appointment_plan_keeps_the_announcement(demo):
    client, _ = login(roles.DISPATCHER, "dispo")
    stop = appointment()
    save(client, stop, channel=rules.BRIEF, scope=rules.SOME_UNITS, units="Whg 5", von="9", bis="11")
    client.post(reverse("documents:announce_sent", args=[stop.pk]), {"done": "1"})
    services.save_draft(services.draft_from_tour(Tour.objects.get(pk=stop.tour_id)), None, confirm=False)
    again = TourStop.objects.get(tour_id=stop.tour_id, building_id=stop.building_id, kind=StopKind.READING)
    assert (again.notice_channel, again.notice_units, again.notice_from) == (rules.BRIEF, "Whg 5", datetime.time(9))
    assert again.notice_sent_at is not None


def test_roles(demo):
    lead, _ = login(roles.MANAGEMENT, "leitung")
    stop = appointment()
    assert lead.get(reverse("documents:announce")).status_code == 200
    assert save(lead, stop, channel=rules.AUSHANG).status_code == 403
    reader, _ = login(roles.READER, "abl")
    assert reader.get(reverse("documents:announce")).status_code == 403
    menu = login(roles.PROCESSING, "sb2")[0].get(reverse("planning:calendar")).content.decode()
    assert "📄 Aushänge &amp; Ankündigungen" in menu
