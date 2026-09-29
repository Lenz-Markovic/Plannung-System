"""Working time over 7,5 h or under 6 h: only information when a person plans."""

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
from planning.models import Employee, Tour
from planning.tests.test_planning_flow import FakeTomTom

pytestmark = pytest.mark.django_db
DAY = datetime.date(2026, 11, 3)


@pytest.fixture
def planner(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: datetime.date(2026, 9, 28))
    monkeypatch.setattr(services, "get_client", lambda: FakeTomTom())  # "TomTom" times: only the time matters
    user = User.objects.create_user(username="dispo")
    user.groups.add(Group.objects.get(name=roles.DISPATCHER))
    client = Client()
    client.force_login(user)
    return client


def start_plan(client, count):
    reader = Employee.objects.filter(can_read=True).exclude(tours__date=DAY).first()
    for building in Building.objects.filter(region="Region Calw", tour_stops__isnull=True).order_by("file_number")[:count]:
        client.post(reverse("planning:select"), {"building": building.pk, "checked": "on"})
    client.post(reverse("planning:dialog"), {"employee": reader.pk, "date": DAY.isoformat(), "start": "08:00",
                                             "break_minutes": 30, "strategy": "far"})
    return reader


def test_short_day_is_only_an_info_and_can_be_confirmed_directly(planner):
    reader = start_plan(planner, 1)
    page = planner.get(reverse("planning:draft")).content.decode()
    assert "nicht ausgelastet" in page and "Zur Info" in page
    assert "Arbeitszeit so übernehmen" not in page  # no approval any more

    toast = planner.post(reverse("planning:draft_action"), {"action": "sort", "strategy": "far"}).content.decode()
    assert 'class="toast-msg info"' in toast and "⏱" in toast

    planner.post(reverse("planning:draft_save"), {"confirm": "1"})
    tour = Tour.objects.get(employee=reader, date=DAY)
    assert tour.status == "confirmed" and tour.time_state == "under"


def test_saved_plan_shows_the_info_in_calendar_and_side_panel(planner):
    reader = start_plan(planner, 1)
    planner.post(reverse("planning:draft_save"), {"confirm": "1"})
    tour = Tour.objects.get(employee=reader, date=DAY)
    event = next(e for e in calendar_events(DAY, DAY + datetime.timedelta(days=1), Employee.objects.all(), True) if e.get("id") == tour.pk)
    assert "⏱" in event["title"] and "weniger als 6 h" in event["extendedProps"]["tooltip"]
    assert "(zur Info)" in planner.get(reverse("planning:tour_detail", args=[tour.pk])).content.decode()
