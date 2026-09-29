"""🤖 Automatic planning with the demo data: proposals, changes, saving."""

import datetime

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from buildings.models import Building, BuildingStatus, InstallationOrder
from core import roles
from planning import services
from planning.models import Absence, Employee, StopKind, Tour

pytestmark = pytest.mark.django_db
MON = datetime.date(2026, 10, 5)
FRI = MON + datetime.timedelta(days=4)


@pytest.fixture
def demo(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: datetime.date(2026, 9, 28))
    monkeypatch.setattr(services, "get_client", lambda: None)


@pytest.fixture
def dispo(demo):
    user = User.objects.create_user(username="dispo")
    user.groups.add(Group.objects.get(name=roles.DISPATCHER))
    client = Client()
    client.force_login(user)
    return client


def test_proposal_keeps_to_7_5_hours_and_uses_only_free_days(demo):
    person = Employee.objects.filter(can_read=True).first()
    Absence.objects.create(employee=person, start_date=MON, end_date=MON)
    proposal = services.autoplan(Employee.objects.filter(active=True), MON, FRI)
    assert proposal["days"]
    assert all(d["work"] + d["drive"] <= 450 for d in proposal["days"])
    assert not any(d["employee"] == person.pk and d["date"] == MON.isoformat() for d in proposal["days"])
    busy = {(t.employee_id, t.date.isoformat()) for t in Tour.objects.filter(date__gte=MON, date__lte=FRI)}
    assert not any((d["employee"], d["date"]) in busy for d in proposal["days"])
    assert all(datetime.date.fromisoformat(d["date"]).weekday() < 5 for d in proposal["days"])
    # every stop at most once, and only unplanned, not released buildings
    keys = [(s["kind"], s.get("building") or s.get("order")) for d in proposal["days"] for s in d["stops"]]
    assert len(keys) == len(set(keys))
    for kind, pk in keys:
        if kind == StopKind.READING:
            building = Building.objects.get(pk=pk)
            assert building.status != BuildingStatus.RELEASED
            assert not building.tour_stops.filter(kind=StopKind.READING).exists()


def test_only_montage_for_installers(demo):
    proposal = services.autoplan(Employee.objects.filter(active=True), MON, FRI, "installation")
    assert proposal["days"] and all(s["kind"] == StopKind.INSTALLATION for d in proposal["days"] for s in d["stops"])
    installers = set(Employee.objects.filter(can_install=True).values_list("pk", flat=True))
    assert {d["employee"] for d in proposal["days"]} <= installers


def test_installation_keeps_8_days_before_the_reading(demo):
    readings = {s.building_id: s.tour.date for s in services.TourStop.objects.filter(kind=StopKind.READING).select_related("tour")}
    proposal = services.autoplan(Employee.objects.filter(active=True), MON, MON + datetime.timedelta(days=30), "installation")
    for day in proposal["days"]:
        for stop in day["stops"]:
            order = InstallationOrder.objects.get(pk=stop["order"])
            if order.building_id in readings:
                assert datetime.date.fromisoformat(day["date"]) <= readings[order.building_id] - datetime.timedelta(days=8)


def test_page_compute_change_open_and_save(dispo):
    page = dispo.get(reverse("planning:autoplan")).content.decode()
    assert "Automatisch planen" in page and "Vorschlag berechnen" in page
    dispo.post(reverse("planning:autoplan"), {"action": "compute", "von": MON.isoformat(), "bis": FRI.isoformat()})
    proposal = dispo.session[services.AUTOPLAN_KEY]
    page = dispo.get(reverse("planning:autoplan")).content.decode()
    assert f"{len(proposal['days'])} Tag" in page and "vorläufig erstellen" in page

    # take one stop out of the first day, then drop the second day
    first_stops = len(proposal["days"][0]["stops"])
    dispo.post(reverse("planning:autoplan"), {"action": "remove_stop", "day": 0, "stop": 0})
    proposal = dispo.session[services.AUTOPLAN_KEY]
    assert len(proposal["days"][0]["stops"]) == first_stops - 1 or first_stops == 1
    days = len(proposal["days"])
    dispo.post(reverse("planning:autoplan"), {"action": "remove_day", "day": 1})
    assert len(dispo.session[services.AUTOPLAN_KEY]["days"]) == days - 1

    # open one day in "Fahrplan prüfen"
    day = dispo.session[services.AUTOPLAN_KEY]["days"][0]
    response = dispo.post(reverse("planning:autoplan"), {"action": "open", "day": 0})
    assert response.url == reverse("planning:draft")
    draft = dispo.session[services.DRAFT_KEY]
    assert draft["employee"] == day["employee"] and draft["date"] == day["date"]

    # save the rest as provisional plans
    rest = dispo.session[services.AUTOPLAN_KEY]["days"]
    response = dispo.post(reverse("planning:autoplan"), {"action": "save_all"})
    assert reverse("planning:calendar") in response.url
    for d in rest:
        tour = Tour.objects.get(employee_id=d["employee"], date=d["date"])
        assert tour.status == "provisional" and tour.stops.count() == len(d["stops"])
    assert services.AUTOPLAN_KEY not in dispo.session


def test_wrong_period_and_permissions(dispo):
    dispo.post(reverse("planning:autoplan"), {"action": "compute", "von": FRI.isoformat(), "bis": MON.isoformat()})
    assert services.AUTOPLAN_KEY not in dispo.session
    reader = User.objects.create_user(username="abl")
    reader.groups.add(Group.objects.get(name=roles.READER))
    client = Client()
    client.force_login(reader)
    assert client.get(reverse("planning:autoplan")).status_code == 403


def test_automatic_plans_create_no_new_conflicts(demo):
    from conflicts.models import Conflict

    def open_conflicts():
        return Conflict.objects.filter(severity__in=["critical", "warning"]).count()

    before = open_conflicts()
    proposal = services.autoplan(Employee.objects.filter(active=True), MON, MON + datetime.timedelta(days=27))
    saved, problems = services.autoplan_save_all(proposal, None)
    assert saved and not problems
    assert open_conflicts() <= before  # the system keeps the montage rule itself
