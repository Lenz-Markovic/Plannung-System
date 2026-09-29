"""Working time over 7,5 h or under 6 h: notice, approval by the planner, stored on the plan."""

import datetime

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from buildings.models import Building
from core import roles
from planning import services
from planning.calendar import calendar_events
from planning.excel import build_workbook
from planning.models import Employee, Tour
from planning.tests.test_planning_flow import FakeTomTom

pytestmark = pytest.mark.django_db
DAY = datetime.date(2026, 11, 3)


@pytest.fixture
def planner(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: datetime.date(2026, 9, 28))
    monkeypatch.setattr(services, "get_client", lambda: FakeTomTom())  # "TomTom" times: only the time decides
    user = User.objects.create_user(username="dispo")
    user.groups.add(Group.objects.get(name=roles.DISPATCHER))
    client = Client()
    client.force_login(user)
    return client


def start_plan(client, count):
    """A draft with `count` unplanned buildings for a free reader on DAY."""
    reader = Employee.objects.filter(can_read=True).exclude(tours__date=DAY).first()
    for building in Building.objects.filter(region="Region Calw", tour_stops__isnull=True).order_by("file_number")[:count]:
        client.post(reverse("planning:select"), {"building": building.pk, "checked": "on"})
    client.post(reverse("planning:dialog"), {"employee": reader.pk, "date": DAY.isoformat(), "start": "08:00",
                                             "break_minutes": 30, "strategy": "far"})
    return reader


def test_short_day_shows_a_notice_and_can_be_approved(planner):
    reader = start_plan(planner, 1)
    page = planner.get(reverse("planning:draft")).content.decode()
    assert "nicht ausgelastet" in page and "Arbeitszeit so übernehmen" in page
    assert planner.post(reverse("planning:draft_save"), {"confirm": "1"}).status_code == 302
    assert not Tour.objects.filter(employee=reader, date=DAY).exists()  # refused: not approved yet

    # every change shows the notification (toast) again
    toast = planner.post(reverse("planning:draft_action"), {"action": "sort", "strategy": "far"}).content.decode()
    assert "⏱" in toast and 'id="toast"' in toast

    approved = planner.post(reverse("planning:draft_action"), {"action": "approve_time", "note": "halber Tag"}).content.decode()
    assert "bewusst so übernommen" in approved and "halber Tag" in approved
    planner.post(reverse("planning:draft_save"), {"confirm": "1"})
    tour = Tour.objects.get(employee=reader, date=DAY)
    assert tour.status == "confirmed" and tour.time_approved_by.username == "dispo"
    assert tour.time_approval_note == "halber Tag" and tour.time_state == "under" and not tour.time_open


def test_approval_is_void_when_the_plan_changes(planner):
    start_plan(planner, 2)
    planner.post(reverse("planning:draft_action"), {"action": "approve_time"})
    assert "bewusst so übernommen" in planner.get(reverse("planning:draft")).content.decode()
    planner.post(reverse("planning:draft_action"), {"action": "remove", "index": 1})  # other net time
    page = planner.get(reverse("planning:draft")).content.decode()
    assert "Arbeitszeit so übernehmen" in page and "bewusst so übernommen" not in page


def test_revoke(planner):
    start_plan(planner, 1)
    planner.post(reverse("planning:draft_action"), {"action": "approve_time"})
    planner.post(reverse("planning:draft_action"), {"action": "revoke_time"})
    assert "Arbeitszeit so übernehmen" in planner.get(reverse("planning:draft")).content.decode()


def test_calendar_side_panel_and_excel_show_the_state(planner):
    reader = start_plan(planner, 1)
    planner.post(reverse("planning:draft_save"), {"confirm": "0"})  # provisional, not approved
    tour = Tour.objects.get(employee=reader, date=DAY)
    event = next(e for e in calendar_events(DAY, DAY + datetime.timedelta(days=1), Employee.objects.all(), True) if e.get("id") == tour.pk)
    assert "⏱" in event["title"] and "weniger als 6 h" in event["extendedProps"]["tooltip"]
    assert "noch nicht freigegeben" in planner.get(reverse("planning:tour_detail", args=[tour.pk])).content.decode()

    tour.time_approved_by, tour.time_approved_at = User.objects.get(username="dispo"), timezone.now()
    tour.save()
    event = next(e for e in calendar_events(DAY, DAY + datetime.timedelta(days=1), Employee.objects.all(), True) if e.get("id") == tour.pk)
    assert "⏱" not in event["title"] and "bewusst übernommen von dispo" in event["extendedProps"]["tooltip"]
    assert "bewusst übernommen von dispo" in planner.get(reverse("planning:tour_detail", args=[tour.pk])).content.decode()
    ws = build_workbook([tour])[0].active
    assert "bewusst übernommen von dispo" in next(c.value for c in ws["A"] if c.value and "Netto" in str(c.value))


def test_recalculating_keeps_the_approval_if_the_time_stays(planner):
    reader = start_plan(planner, 1)
    planner.post(reverse("planning:draft_action"), {"action": "approve_time", "note": "ok"})
    planner.post(reverse("planning:draft_save"), {"confirm": "1"})
    tour = Tour.objects.get(employee=reader, date=DAY)
    draft = services.draft_from_tour(tour)
    assert services.calculate_preview(draft).time_approval["note"] == "ok"
