"""Status workflow and 14-day deadline through the web interface."""

import datetime
import io

import pytest
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from buildings.models import Building, PropertyManager
from core import roles
from documents.models import CostDocumentReceipt

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2026, 9, 28)


@pytest.fixture(autouse=True)
def demo_data(monkeypatch):
    """Demo data + a fixed 'today', so the results do not depend on the real date."""
    monkeypatch.setattr(timezone, "localdate", lambda *args: TODAY)
    call_command("import_prototype", stdout=io.StringIO())


def login(client, role):
    user = User.objects.create_user(username=role.replace("/", "_"), password="x")
    user.groups.add(Group.objects.get(name=role))
    client.force_login(user)
    return user


def post_row(client, building, **data):
    return client.post(reverse("buildings:update", args=[building.pk]), data, HTTP_HX_REQUEST="true")


def warning_count(client):
    return client.get(reverse("documents:warning")).context["total"]


def test_warning_shows_the_10_overdue_buildings_like_the_prototype(client):
    login(client, roles.PROCESSING)
    response = client.get(reverse("documents:warning"))
    assert response.context["total"] == 10
    assert len(response.context["shown"]) == 3  # "... und 7 weitere"
    html = response.content.decode()
    assert "seit 13 Tagen überfällig" in html
    assert "schließen" not in html.lower()  # no close button


def test_management_gets_no_warning(client):
    login(client, roles.MANAGEMENT)
    assert client.get(reverse("documents:warning")).content == b""


def test_status_change_restarts_deadline_and_removes_warning(client):
    user = login(client, roles.PROCESSING)
    overdue = client.get(reverse("documents:warning")).context["shown"][0].building
    response = client.post(reverse("documents:warning_status", args=[overdue.pk]), {"status": "rework"})
    assert response.context["total"] == 9
    receipt = CostDocumentReceipt.objects.get(building=overdue)
    assert (receipt.deadline_start, receipt.reset_reason) == (TODAY, "status")
    overdue.refresh_from_db()
    assert overdue.history.first().history_user == user  # who changed it


def test_dispatcher_may_not_release(client):
    login(client, roles.DISPATCHER)
    building = Building.objects.get(file_number="0798615")
    assert post_row(client, building, status="released").status_code == 403
    assert post_row(client, building, status="rework").status_code == 200
    building.refresh_from_db()
    assert building.status == "rework"


def test_show_in_list_hides_card_and_redirects(client):
    login(client, roles.PROCESSING)
    first = client.get(reverse("documents:warning")).context["shown"][0].building
    response = client.post(reverse("documents:warning_show", args=[first.pk]))
    assert response["HX-Redirect"].endswith(f"?q={first.file_number}")
    assert first not in [e.building for e in client.get(reverse("documents:warning")).context["shown"]]


def test_enter_and_remove_received_date_in_table(client):
    login(client, roles.PROCESSING)
    building = Building.objects.get(file_number="0798615")
    response = post_row(client, building, received_on="2026-09-28")
    assert "noch 14 Tage" in response.content.decode()
    assert response["HX-Trigger"] == "deadlines-changed, buildings-changed"
    assert CostDocumentReceipt.objects.get(building=building).deadline_start == TODAY
    post_row(client, building, received_on="")
    assert not CostDocumentReceipt.objects.filter(building=building).exists()


def test_property_manager_and_note(client):
    login(client, roles.PROCESSING)
    building = Building.objects.get(file_number="0798615")
    post_row(client, building, property_manager="  Neue   Verwaltung GmbH ")
    post_row(client, building, note="Hausmeister vorher anrufen")
    building.refresh_from_db()
    assert building.property_manager == PropertyManager.objects.get(name="Neue Verwaltung GmbH")
    assert building.note == "Hausmeister vorher anrufen"


def test_management_cannot_edit(client):
    login(client, roles.MANAGEMENT)
    building = Building.objects.get(file_number="0798615")
    assert post_row(client, building, note="x").status_code == 403


def test_receipt_list_counts(client):
    login(client, roles.PROCESSING)
    counts = client.get(reverse("documents:list")).context["counts"]
    assert (counts["all"], counts["overdue"]) == (19, 10)
    shown = client.get(reverse("documents:list"), {"f": "ueber"}).context["entries"]
    assert len(shown) == 10 and all(e.info.state == "overdue" for e in shown)
