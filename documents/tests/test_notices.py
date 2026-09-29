"""Tenant notices (Aushang): optional per stop, company template (page + Word), marking, outdated after changes."""

import datetime
import io
import re
import zipfile

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from buildings.models import InstallationOrder
from core import roles
from documents import notices
from planning import services
from planning.models import StopKind, Tour

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: datetime.date(2026, 9, 28))
    monkeypatch.setattr(services, "get_client", lambda: None)


def login(role, name):
    user = User.objects.create_user(username=name)
    user.groups.add(Group.objects.get(name=role))
    client = Client()
    client.force_login(user)
    return client


def planned_tour():
    """A saved plan with times (so the notice has a time window)."""
    tour = Tour.objects.filter(stops__kind=StopKind.READING).order_by("date").first()
    return services.save_draft(services.draft_from_tour(tour), None, confirm=False)


def panel(client, tour):
    return client.get(reverse("planning:tour_detail", args=[tour.pk])).content.decode()


def switch_on(client, stops):
    return client.post(reverse("documents:notice_toggle"), {"stop": [s.pk for s in stops], "on": "1"})


def test_notice_is_optional_off_by_default(demo):
    dispo = login(roles.DISPATCHER, "dispo")
    tour = planned_tour()
    html = panel(dispo, tour)
    assert "Für keinen Stopp gewählt" in html and "＋ Aushang" in html
    assert "noch nicht gedruckt" not in html and "Aushänge drucken" not in html  # no warning without a choice

    stop = tour.stops.exclude(kind=StopKind.HELP).first()
    html = switch_on(dispo, [stop]).content.decode()  # the panel comes back
    assert "Aushänge drucken (1)" in html and "noch nicht gedruckt" in html
    stop.refresh_from_db()
    assert stop.notice_wanted

    dispo.post(reverse("documents:notice_toggle"), {"stop": [stop.pk], "on": "0"})
    stop.refresh_from_db()
    assert not stop.notice_wanted


def test_print_marks_the_chosen_stops_and_shows_the_template(demo):
    dispo = login(roles.DISPATCHER, "dispo")
    tour = planned_tour()
    stops = list(tour.stops.exclude(kind=StopKind.HELP))
    switch_on(dispo, stops)
    response = dispo.post(reverse("documents:notice_print"), {"stop": [s.pk for s in stops]})
    page = dispo.get(response.url).content.decode()
    assert page.count('<section class="page">') == len(stops)
    assert "img/aushang_vorlage.jpg" in page  # the picture of the company template
    first = stops[0]
    assert first.building.file_number in page                   # top left: Liegenschaftsnummer
    days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"]
    assert f'>{days[tour.date.weekday()]}</span>' in page      # am: weekday
    assert f'>{tour.date:%d.%m.%Y}</span>' in page               # dem: date
    assert re.search(r">\d\d:00 – \d\d:00 Uhr</span>", page)     # zwischen / ab
    assert 'class="box ablesung on"' in page
    for stop in tour.stops.exclude(kind=StopKind.HELP):
        assert stop.notice_printed_at and f"{tour.date:%d.%m.%Y}, zwischen" in stop.notice_for
    assert "alle gedruckt" in panel(dispo, tour)


def test_word_file_is_the_filled_template(demo):
    dispo = login(roles.DISPATCHER, "dispo")
    tour = planned_tour()
    stops = list(tour.stops.exclude(kind=StopKind.HELP))[:2]
    response = dispo.post(reverse("documents:notice_print"), {"stop": [s.pk for s in stops], "format": "docx"})
    assert response["Content-Type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert "attachment" in response["Content-Disposition"]
    package = zipfile.ZipFile(io.BytesIO(response.content))
    xml = package.read("word/document.xml").decode()
    assert xml.count("<w:pageBreakBefore/>") == len(stops) - 1  # one page per notice
    assert stops[0].building.file_number in xml and f"{tour.date:%d.%m.%Y}" in xml
    assert b"document.main+xml" in package.read("[Content_Types].xml")
    assert "word/media/image1.jpeg" in package.namelist()  # the design of the template is kept
    stops[0].refresh_from_db()
    assert stops[0].notice_printed_at and stops[0].notice_wanted  # printing = chosen


def test_installation_ticks_montage_or_austausch(demo):
    order = InstallationOrder.objects.filter(items__category__code="RWM").first()
    tour = Tour.objects.filter(stops__kind=StopKind.READING).order_by("date").first()
    draft = services.draft_from_tour(tour)
    services.add_stop(draft, "installation", order.pk)
    saved = services.save_draft(draft, None, confirm=False)
    stop = saved.stops.get(installation_order=order)
    fields = notices.fields_of(stop)
    assert ("austausch" if order.work_type == "exchange" else "montage") in fields.boxes
    assert "rwm" in fields.boxes and "ablesung" not in fields.boxes


def test_moving_the_plan_makes_the_notice_outdated(demo):
    dispo = login(roles.DISPATCHER, "dispo")
    tour = planned_tour()
    dispo.post(reverse("documents:notice_print"), {"stop": [s.pk for s in tour.stops.all()]})
    new_day = tour.date + datetime.timedelta(days=1)
    while Tour.objects.filter(employee=tour.employee, date=new_day).exists() or new_day.weekday() >= 5:
        new_day += datetime.timedelta(days=1)
    moved = services.save_draft(services.draft_from_tour(tour, date=new_day), None, confirm=False)
    assert all(s.notice_printed_at and s.notice_wanted for s in moved.stops.exclude(kind=StopKind.HELP))  # kept ...
    assert "Aushang veraltet" in panel(dispo, moved)  # ... but marked as outdated: new day


def test_the_choice_stays_when_the_plan_is_saved_again(demo):
    dispo = login(roles.DISPATCHER, "dispo")
    tour = planned_tour()
    stop = tour.stops.exclude(kind=StopKind.HELP).first()
    switch_on(dispo, [stop])
    again = services.save_draft(services.draft_from_tour(tour), None, confirm=False)
    assert again.stops.filter(notice_wanted=True).count() == 1


def test_late_warning_only_for_chosen_stops(demo, monkeypatch):
    dispo = login(roles.DISPATCHER, "dispo")
    tour = planned_tour()
    monkeypatch.setattr(timezone, "localdate", lambda *args: tour.date - datetime.timedelta(days=3))
    assert "Aushang fehlt – schon zu spät" not in panel(dispo, tour)  # no warning per stop without a choice
    switch_on(dispo, [tour.stops.exclude(kind=StopKind.HELP).first()])
    assert "Aushang fehlt – schon zu spät" in panel(dispo, tour)


def test_permissions(demo):
    tour = planned_tour()
    stop = tour.stops.first()
    reader = login(roles.READER, "abl")
    assert reader.get(reverse("documents:notice_page"), {"stop": stop.pk}).status_code == 403
    chef = login(roles.MANAGEMENT, "chef")  # may look, but not choose or mark as printed
    assert chef.get(reverse("documents:notice_page"), {"stop": stop.pk}).status_code == 200
    assert chef.post(reverse("documents:notice_print"), {"stop": [stop.pk]}).status_code == 403
    assert chef.post(reverse("documents:notice_toggle"), {"stop": [stop.pk], "on": "1"}).status_code == 403


def test_print_any_time_but_ask_when_too_late(demo, monkeypatch):
    dispo = login(roles.DISPATCHER, "dispo")
    tour = planned_tour()
    stop = tour.stops.exclude(kind=StopKind.HELP).first()
    html = panel(dispo, tour)
    assert "gleich drucken" in html  # printing works without "＋ Aushang" first
    monkeypatch.setattr(timezone, "localdate", lambda *args: tour.date - datetime.timedelta(days=5))
    html = panel(dispo, tour)
    assert reverse("documents:notice_confirm") in html  # too late: the button asks first
    question = dispo.get(reverse("documents:notice_confirm"), {"stop": [stop.pk]}).content.decode()
    assert "schon zu spät" in question and "nur noch <b>5 Tage</b>" in question and "Trotzdem drucken" in question
    word = dispo.get(reverse("documents:notice_confirm"), {"stop": [stop.pk], "format": "docx"}).content.decode()
    assert "Trotzdem als Word laden" in word and 'name="format" value="docx"' in word
    dispo.post(reverse("documents:notice_print"), {"stop": [stop.pk]})  # "Trotzdem drucken"
    stop.refresh_from_db()
    assert stop.notice_printed_at and stop.notice_wanted


def test_in_time_prints_directly(demo, monkeypatch):
    dispo = login(roles.DISPATCHER, "dispo")
    tour = planned_tour()
    monkeypatch.setattr(timezone, "localdate", lambda *args: tour.date - datetime.timedelta(days=30))
    html = panel(dispo, tour)
    assert reverse("documents:notice_confirm") not in html and 'action="/unterlagen/aushang/drucken/"' in html
