"""Tenant notices (Aushang): print page, marking, outdated after changes, settings."""

import datetime

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core import roles
from documents.models import NoticeSettings
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


def test_print_marks_the_stops_and_shows_one_page_each(demo):
    dispo = login(roles.DISPATCHER, "dispo")
    tour = planned_tour()
    stops = list(tour.stops.exclude(kind=StopKind.HELP))
    panel = dispo.get(reverse("planning:tour_detail", args=[tour.pk])).content.decode()
    assert "Aushang für die Mieter" in panel and "noch nicht gedruckt" in panel

    response = dispo.post(reverse("documents:notice_print"), {"stop": [s.pk for s in stops]})
    page = dispo.get(response.url).content.decode()
    assert page.count('class="page"') == len(stops)
    assert "Wichtige Information" in page and "zwischen" in page and "Uhr" in page
    for stop in tour.stops.exclude(kind=StopKind.HELP):
        assert stop.notice_printed_at and f"{tour.date:%d.%m.%Y}, zwischen" in stop.notice_for
    assert "alle gedruckt" in dispo.get(reverse("planning:tour_detail", args=[tour.pk])).content.decode()


def test_moving_the_plan_makes_the_notice_outdated(demo):
    dispo = login(roles.DISPATCHER, "dispo")
    tour = planned_tour()
    dispo.post(reverse("documents:notice_print"), {"stop": [s.pk for s in tour.stops.all()]})
    new_day = tour.date + datetime.timedelta(days=1)
    while Tour.objects.filter(employee=tour.employee, date=new_day).exists() or new_day.weekday() >= 5:
        new_day += datetime.timedelta(days=1)
    moved = services.save_draft(services.draft_from_tour(tour, date=new_day), None, confirm=False)
    assert all(s.notice_printed_at for s in moved.stops.exclude(kind=StopKind.HELP))  # kept ...
    panel = dispo.get(reverse("planning:tour_detail", args=[moved.pk])).content.decode()
    assert "Aushang veraltet" in panel  # ... but marked as outdated: new day


def test_late_warning(demo, monkeypatch):
    dispo = login(roles.DISPATCHER, "dispo")
    tour = planned_tour()
    monkeypatch.setattr(timezone, "localdate", lambda *args: tour.date - datetime.timedelta(days=3))
    panel = dispo.get(reverse("planning:tour_detail", args=[tour.pk])).content.decode()
    assert "schon zu spät" in panel


def test_contact_from_the_settings_and_access_texts(demo):
    settings = NoticeSettings.load()
    settings.company, settings.phone, settings.extra_text = "Muster Messdienst", "07000 123", "Bitte Heizung nicht abdrehen."
    settings.save()
    dispo = login(roles.DISPATCHER, "dispo")
    tour = planned_tour()
    stop = next(s for s in tour.stops.filter(kind=StopKind.READING) if s.building.access.apartment)
    page = dispo.get(reverse("documents:notice_page"), {"stop": stop.pk}).content.decode()
    assert "Muster Messdienst" in page and "07000 123" in page and "Bitte Heizung nicht abdrehen." in page
    assert "Wir müssen Ihre Wohnung betreten" in page and "Passt der Termin nicht?" in page


def test_permissions(demo):
    tour = planned_tour()
    stop = tour.stops.first()
    reader = login(roles.READER, "abl")
    assert reader.get(reverse("documents:notice_page"), {"stop": stop.pk}).status_code == 403
    chef = login(roles.MANAGEMENT, "chef")  # may look, but not mark as printed
    assert chef.get(reverse("documents:notice_page"), {"stop": stop.pk}).status_code == 200
    assert chef.post(reverse("documents:notice_print"), {"stop": [stop.pk]}).status_code == 403


def test_admin_edits_the_settings(demo):
    admin_user = User.objects.create_user(username="chefadmin", is_staff=True)
    admin_user.groups.add(Group.objects.get(name=roles.ADMIN))
    client = Client()
    client.force_login(admin_user)
    page = client.get(reverse("admin:documents_noticesettings_changelist"), follow=True).content.decode()
    assert "Aushang-Einstellungen" in page and "Firma" in page
