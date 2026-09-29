"""🕘 Verlauf: who changed what - plans, appointments, printed notices, orders, status; by role."""

import datetime

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from buildings.models import Building, InstallationOrder
from buildings.services import change_status
from core import roles
from journal.models import Activity, ActivityKind
from planning import services
from planning.models import StopKind, Tour

pytestmark = pytest.mark.django_db


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


def a_tour():
    return Tour.objects.filter(stops__kind=StopKind.READING).order_by("date").first()


def free_day(tour):
    day = tour.date + datetime.timedelta(days=1)
    while Tour.objects.filter(employee=tour.employee, date=day).exists() or day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    return day


def test_changing_and_moving_a_plan(demo):
    dispo = user(roles.DISPATCHER, "dispo")
    tour = a_tour()
    services.save_draft(services.draft_from_tour(tour), dispo, confirm=False)
    changed = Activity.objects.filter(kind=ActivityKind.PLAN).first()
    assert changed.text.startswith("Fahrplan geändert") and changed.user == dispo and changed.tour_id == tour.pk
    new_day = free_day(tour)
    tour.refresh_from_db()  # the new version after the first save
    services.save_draft(services.draft_from_tour(tour, date=new_day), dispo, confirm=False)
    moved = Activity.objects.filter(kind=ActivityKind.PLAN).first()
    assert moved.text.startswith("Termin verschoben:") and f"{new_day:%d.%m.%Y}" in moved.text


def test_deleting_and_notices_and_order_and_status(demo):
    dispo_user = user(roles.DISPATCHER, "dispo")
    dispo = client_for(dispo_user)
    tour = a_tour()
    stop = tour.stops.exclude(kind=StopKind.HELP).first()
    dispo.post(reverse("documents:notice_print"), {"stop": [stop.pk]})
    printed = Activity.objects.filter(kind=ActivityKind.NOTICE).first()
    assert printed.text.startswith("Aushang gedruckt (1)") and printed.user == dispo_user

    order = InstallationOrder.objects.first()
    dispo.post(reverse("orders:update", args=[order.pk]), {"priority": "prio_1"})
    assert Activity.objects.filter(kind=ActivityKind.ORDER, installation_order=order, text__contains="Priorität → Prio 1").exists()

    building = Building.objects.filter(status="open").first()
    change_status(building, "rework", dispo_user)
    assert Activity.objects.filter(kind=ActivityKind.STATUS, building=building, text__contains="→ Nacharbeit").exists()

    other = Tour.objects.exclude(pk=tour.pk).first()
    dispo.post(reverse("planning:tour_delete", args=[other.pk]))
    deleted = Activity.objects.filter(kind=ActivityKind.PLAN).first()
    assert deleted.text.startswith("Fahrplan gelöscht") and deleted.tour is None  # the text stays


def test_drawer_by_role_and_filters(demo):
    dispo_user = user(roles.DISPATCHER, "dispo")
    tour = a_tour()
    services.save_draft(services.draft_from_tour(tour), dispo_user, confirm=False)
    for role in (roles.ADMIN, roles.DISPATCHER, roles.PROCESSING, roles.MANAGEMENT):
        assert client_for(user(role, f"u-{role}")).get(reverse("journal:activity")).status_code == 200
    assert client_for(user(roles.READER, "abl")).get(reverse("journal:activity")).status_code == 403

    chef = client_for(user(roles.MANAGEMENT, "chef"))
    html = chef.get(reverse("journal:activity")).content.decode()
    assert "Fahrplan geändert" in html and "dispo" in html
    assert "Fahrplan geändert" not in chef.get(reverse("journal:activity"), {"bereich": "notice"}).content.decode()
    assert "Fahrplan geändert" not in chef.get(reverse("journal:activity"), {"wer": "ich"}).content.decode()
    assert "Fahrplan geändert" in chef.get(reverse("journal:activity"), {"tour": tour.pk}).content.decode()
    panel = client_for(dispo_user).get(reverse("planning:tour_detail", args=[tour.pk])).content.decode()
    assert "wer hat was geändert?" in panel and "Fahrplan geändert" in panel and "zuletzt:" in panel


def test_verlauf_is_behind_the_user_name_not_a_button(demo):
    page = client_for(user(roles.DISPATCHER, "dispo")).get(reverse("orders:list")).content.decode()
    assert 'class="user user-menu"' in page and "🕘 Verlauf – wer hat was geändert?" in page
    assert 'class="tab linkish"' not in page


def test_printed_by_whom(demo):
    dispo_user = user(roles.DISPATCHER, "dispo")
    tour = a_tour()
    stop = tour.stops.exclude(kind=StopKind.HELP).first()
    client_for(dispo_user).post(reverse("documents:notice_print"), {"stop": [stop.pk]})
    stop.refresh_from_db()
    assert stop.notice_printed_by == dispo_user
    panel = client_for(dispo_user).get(reverse("planning:tour_detail", args=[tour.pk])).content.decode()
    assert "Aushang gedruckt" in panel and "von dispo" in panel
    again = services.save_draft(services.draft_from_tour(Tour.objects.get(pk=tour.pk)), dispo_user, confirm=False)
    assert again.stops.filter(notice_printed_by=dispo_user).count() == 1  # kept when the plan is saved again


def test_reader_sees_no_aushang_and_no_verlauf(demo):
    tour = a_tour()
    stop = tour.stops.exclude(kind=StopKind.HELP).first()
    client_for(user(roles.DISPATCHER, "dispo")).post(reverse("documents:notice_print"), {"stop": [stop.pk]})
    reader = user(roles.READER, "abl")
    tour.employee.user = reader
    tour.employee.save()
    panel = client_for(reader).get(reverse("planning:tour_detail", args=[tour.pk])).content.decode()
    assert "Aushang" not in panel and "wer hat was geändert" not in panel
    page = client_for(reader).get(reverse("planning:my_day")).content.decode()
    assert "Aushang" not in page and "user-menu" not in page
