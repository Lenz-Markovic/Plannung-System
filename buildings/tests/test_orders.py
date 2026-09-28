"""Montageaufträge list, montage planning and the Konflikte page."""

import datetime
import io

import pytest
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from buildings.models import InstallationOrder, OrderStatus
from conflicts.models import Conflict
from core import roles
from planning import services
from planning.models import Employee, StopKind, Tour

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: datetime.date(2026, 9, 28))
    monkeypatch.setattr(services, "get_client", lambda: None)  # no TomTom: estimated drives
    call_command("import_prototype", stdout=io.StringIO())


def login(role, name):
    user = User.objects.create_user(username=name)
    user.groups.add(Group.objects.get(name=role))
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def dispo(demo):
    return login(roles.DISPATCHER, "dispo")


def unplanned_order_with_reading():
    """An order without appointment whose building already has a reading date."""
    for order in InstallationOrder.objects.filter(tour_stops__isnull=True).exclude(status=OrderStatus.DONE):
        if order.building and order.building.tour_stops.filter(kind=StopKind.READING).exists():
            return order, order.building.tour_stops.get(kind=StopKind.READING).tour.date
    raise AssertionError("no such order in the demo data")


def free_installer(date):
    return Employee.objects.filter(can_install=True).exclude(tours__date=date).first()


# --- list ------------------------------------------------------------------------------

def test_list_shows_all_orders_and_filters(dispo):
    html = dispo.get(reverse("orders:list")).content.decode()
    assert html.count('id="orow-') == 70 and "🔧 Montage" in html
    response = dispo.get(reverse("orders:list"), {"konflikt": "offen"}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="order-results")
    with_conflict = set(Conflict.objects.filter(severity__in=["critical", "warning"]).values_list("installation_order", flat=True))
    assert response.content.decode().count('id="orow-') == len(with_conflict) == 5
    assert response["HX-Push-Url"] == "/montage/?konflikt=offen"
    ohne = dispo.get(reverse("orders:list"), {"termin": "ohne"}).context["count"]
    assert ohne == InstallationOrder.objects.filter(tour_stops__isnull=True).count()


def test_search_and_installer_filter(dispo):
    order = InstallationOrder.objects.exclude(assigned_installers=None).first()
    installer = order.assigned_installers.first()
    assert dispo.get(reverse("orders:list"), {"q": order.re_number}).context["count"] == 1
    orders = dispo.get(reverse("orders:list"), {"installer": installer.pk}).context["orders"]
    assert order in orders and all(installer in o.assigned_installers.all() for o in orders)


def test_reader_may_not_open_the_order_list(demo):
    assert login(roles.READER, "abl").get(reverse("orders:list")).status_code == 403


# --- editing -----------------------------------------------------------------------------

def test_edit_priority_status_time_and_installers(dispo):
    order = InstallationOrder.objects.first()
    url = reverse("orders:update", args=[order.pk])
    assert "Priorität gespeichert" in dispo.post(url, {"priority": "prio_1"}).content.decode()
    dispo.post(url, {"duration": "95"})
    installers = list(Employee.objects.filter(can_install=True)[:2])
    dispo.post(url, {"installers_sent": "1", "installers": [e.pk for e in installers]})
    order.refresh_from_db()
    assert (order.priority, order.duration_minutes_manual) == ("prio_1", 95)
    assert set(order.assigned_installers.all()) == set(installers)
    dispo.post(url, {"duration": str(order.duration_minutes_calculated)})  # back to the calculated value
    order.refresh_from_db()
    assert order.duration_minutes_manual is None


def test_wrong_values_are_refused_with_a_message(dispo):
    order = InstallationOrder.objects.first()
    url = reverse("orders:update", args=[order.pk])
    response = dispo.post(url, {"duration": "viel"})
    assert response["HX-Reswap"] == "none" and "Minuten" in response.content.decode()
    four = Employee.objects.filter(can_install=True)[:4]
    assert "Höchstens 3" in dispo.post(url, {"installers_sent": "1", "installers": [e.pk for e in four]}).content.decode()


def test_processing_may_look_but_not_edit(demo):
    client = login(roles.PROCESSING, "sb")
    order = InstallationOrder.objects.first()
    assert client.get(reverse("orders:list")).status_code == 200
    assert client.post(reverse("orders:update", args=[order.pk]), {"priority": "prio_1"}).status_code == 403


def test_done_solves_the_conflict(dispo):
    conflict = Conflict.objects.filter(severity="critical").first()
    dispo.post(reverse("orders:update", args=[conflict.installation_order_id]), {"status": "done"})
    assert not Conflict.objects.filter(pk=conflict.pk).exists()


# --- planning --------------------------------------------------------------------------------

def test_plan_an_order_on_the_reading_day_shows_the_conflict_and_saves_it(dispo):
    order, reading_day = unplanned_order_with_reading()
    installer = free_installer(reading_day)
    bar = dispo.post(reverse("orders:select"), {"order": order.pk, "checked": "on"}).content.decode()
    assert "(1)" in bar
    response = dispo.post(reverse("planning:montage_dialog"), {
        "employee": installer.pk, "date": reading_day.isoformat(), "start": "08:00", "break_minutes": 30, "strategy": "far"})
    assert response["HX-Redirect"] == reverse("planning:draft")
    preview = dispo.get(reverse("planning:draft")).content.decode()
    assert "Montage und Ablesung am selben Tag" in preview and "kf-critical" in preview

    dispo.post(reverse("planning:draft_save"), {"confirm": "0"})
    order.refresh_from_db()
    tour = Tour.objects.get(employee=installer, date=reading_day)
    assert order.status == OrderStatus.PLANNED and tour.stops.get().installation_order == order
    assert Conflict.objects.filter(installation_order=order, rule="same_day").exists()
    assert services.get_order_selection(dispo.session) == []

    # deleting the tour: order open again, conflict gone
    services.delete_tour(tour)
    order.refresh_from_db()
    assert order.status == OrderStatus.OPEN
    assert not Conflict.objects.filter(installation_order=order, rule="same_day").exists()


def test_plan_well_before_the_reading_has_no_finding(dispo):
    order, reading_day = unplanned_order_with_reading()
    day = reading_day - datetime.timedelta(days=21)
    while day.weekday() >= 5:
        day -= datetime.timedelta(days=1)
    draft = services.create_draft([], free_installer(day), day, datetime.time(8), 30, "far", order_ids=[order.pk])
    preview = services.calculate_preview(draft)
    assert preview.stops[0].kind == StopKind.INSTALLATION and preview.stops[0].findings == []


# --- Konflikte page ----------------------------------------------------------------------------

def test_conflict_page_filters_and_acknowledge(dispo):
    page = dispo.get(reverse("conflicts:list"))
    assert page.context["counts"]["critical"] == 4
    conflict = Conflict.objects.filter(severity="critical").first()
    url = reverse("conflicts:acknowledge", args=[conflict.pk])
    assert "Begründung" in dispo.post(url, {"note": ""}).content.decode()  # a reason is required
    response = dispo.post(url, {"note": "mit Kunde abgesprochen"})
    assert "bewusst übernommen" in response.content.decode() and response["HX-Trigger"] == "conflicts-changed"
    assert dispo.get(reverse("conflicts:list")).context["counts"]["critical"] == 3
    assert conflict.pk in [c.pk for c in dispo.get(reverse("conflicts:list"), {"f": "uebernommen"}).context["conflicts"]]
    badge = dispo.get(reverse("conflicts:badge")).content.decode()
    assert ">4<" in badge  # 3 critical + 1 to check
    dispo.post(url, {"reopen": "1"})
    conflict.refresh_from_db()
    assert conflict.acknowledged_at is None


def test_management_sees_conflicts_but_cannot_acknowledge(demo):
    client = login(roles.MANAGEMENT, "chef")
    assert client.get(reverse("conflicts:list")).status_code == 200
    conflict = Conflict.objects.first()
    assert client.post(reverse("conflicts:acknowledge", args=[conflict.pk]), {"note": "x"}).status_code == 403
