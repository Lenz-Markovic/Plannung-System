"""Teams for big objects, the first free day per person, weekly hours only for Admin."""

import datetime
import json

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from buildings.models import Building
from core import roles
from planning import services
from planning.excel import build_workbook
from planning.models import Absence, Employee, Tour

pytestmark = pytest.mark.django_db
DAY = datetime.date(2026, 11, 10)
TODAY = datetime.date(2026, 9, 28)  # a Monday


@pytest.fixture
def demo(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: TODAY)
    monkeypatch.setattr(services, "get_client", lambda: None)


def login(role, name, employee=None):
    user = User.objects.create_user(username=name)
    user.groups.add(Group.objects.get(name=role))
    if employee:
        employee.user = user
        employee.save()
    client = Client()
    client.force_login(user)
    return client


def free_people(count, day=DAY):
    return [e for e in Employee.objects.filter(can_read=True).order_by("short_name")
            if not Tour.objects.filter(services.tours_with(e), date=day).exists()][:count]


def big_building():
    return Building.objects.filter(tour_stops__isnull=True).order_by("-reading_minutes_calculated").first()


def team_draft(lead, mates, building=None):
    draft = services.create_draft([(building or big_building()).pk], lead, DAY, datetime.time(8), 30, "far")
    for mate in mates:
        services.set_team(draft, add=mate.pk)
    return draft


# --- team ------------------------------------------------------------------------------

def test_team_splits_the_work_time_and_is_saved(demo):
    lead, mate = free_people(2)
    building = big_building()
    preview = services.calculate_preview(team_draft(lead, [mate], building))
    assert preview.stops[0].full_minutes == building.reading_minutes
    assert preview.stops[0].work_minutes == services.team_minutes(building.reading_minutes, 2)
    draft = team_draft(lead, [mate], building)
    services.set_team(draft, split=False)
    assert services.calculate_preview(draft).stops[0].work_minutes == building.reading_minutes

    tour = services.save_draft(services.approve_time(team_draft(lead, [mate], building), User(username="d")), None, confirm=False)
    assert tour.people == [lead, mate] and tour.people_label == f"{lead} + {mate}" and tour.split_work
    ws = build_workbook([tour])[0].active
    assert ws["G1"].value == f"{lead} + {mate}"


def test_team_member_who_is_busy_or_absent_is_refused(demo):
    lead, mate, other = free_people(3)
    Tour.objects.create(employee=other, date=DAY)          # other has an own plan that day
    Absence.objects.create(employee=mate, start_date=DAY, end_date=DAY)
    preview = services.calculate_preview(team_draft(lead, [mate, other]))
    assert any("abwesend" in p for p in preview.team_problems)
    assert f"{other} hat an diesem Tag schon einen eigenen Fahrplan." in preview.team_problems
    with pytest.raises(ValueError, match="abwesend"):
        services.save_draft(team_draft(lead, [mate, other]), None, confirm=False)  # also not "vorläufig"


def test_a_team_member_cannot_get_a_second_plan_that_day(demo):
    lead, mate = free_people(2)
    services.save_draft(services.approve_time(team_draft(lead, [mate]), User(username="d")), None, confirm=False)
    own = services.create_draft([Building.objects.filter(tour_stops__isnull=True).first().pk], mate, DAY, datetime.time(8), 30, "far")
    assert any("schon im Plan von" in p for p in services.calculate_preview(own).team_problems)


def test_team_limits(demo):
    lead, *mates = free_people(5)
    draft = team_draft(lead, mates[:3])
    with pytest.raises(ValueError, match="Höchstens 4"):
        services.set_team(draft, add=mates[3].pk)
    with pytest.raises(ValueError, match="schon im Plan"):
        services.set_team(draft, add=lead.pk)


def test_team_in_preview_calendar_and_mein_tag(demo):
    lead, mate = free_people(2)
    dispo = login(roles.DISPATCHER, "dispo")
    dispo.post(reverse("planning:select"), {"building": big_building().pk, "checked": "on"})
    dispo.post(reverse("planning:dialog"), {"employee": lead.pk, "date": DAY.isoformat(), "start": "08:00",
                                            "break_minutes": 30, "strategy": "far"})
    html = dispo.post(reverse("planning:draft_action"), {"action": "team_add", "pk": mate.pk}).content.decode()
    assert "ist jetzt im Team" in html and "÷ 2" in html
    dispo.post(reverse("planning:draft_action"), {"action": "approve_time"})
    dispo.post(reverse("planning:draft_save"), {"confirm": "0"})
    tour = Tour.objects.get(employee=lead, date=DAY)

    feed = json.loads(dispo.get(reverse("planning:calendar_feed"), {"start": "2026-11-09", "end": "2026-11-12", "person": mate.pk}).content)
    assert [e["id"] for e in feed if e.get("id")] == [tour.pk] and "👥" in feed[0]["title"]
    assert mate.short_name not in dispo.get(reverse("planning:day"), {"datum": DAY.isoformat()}).content.decode().split("Noch frei")[1]

    reader = login(roles.READER, "mate", employee=mate)
    day = reader.get(reverse("planning:my_day"), {"datum": DAY.isoformat()}).content.decode()
    assert "Stopp erledigt" in day and f"👥 {tour.people_label}" in day
    stop = tour.stops.first()
    assert reader.post(reverse("planning:stop_done", args=[stop.pk]), {"done": "1"}).status_code == 200


def test_moving_a_team_plan_keeps_the_team(demo):
    lead, mate = free_people(2)
    tour = services.save_draft(services.approve_time(team_draft(lead, [mate]), User(username="d")), None, confirm=False)
    assert services.draft_from_tour(tour, date=DAY + datetime.timedelta(days=1))["team"] == [mate.pk]


# --- first free day --------------------------------------------------------------------

def test_first_free_day_counts_team_plans_and_absences(demo):
    lead, mate = free_people(2, day=TODAY)
    Tour.objects.create(employee=lead, date=TODAY).team.add(mate)                   # Monday: both busy
    Absence.objects.create(employee=mate, start_date=TODAY + datetime.timedelta(days=1), end_date=TODAY + datetime.timedelta(days=2))
    days = services.first_free_days([lead, mate], TODAY)
    assert days[lead] == TODAY + datetime.timedelta(days=1)   # Tuesday
    assert days[mate] == TODAY + datetime.timedelta(days=3)   # Thursday


def test_free_days_panel(demo):
    dispo = login(roles.DISPATCHER, "dispo")
    html = dispo.get(reverse("planning:free_days")).content.decode()
    assert "Erster freier Tag" in html and html.count("free-row") == Employee.objects.filter(active=True).count()
    installers = dispo.get(reverse("planning:free_days"), {"art": "installation"}).content.decode()
    assert installers.count("free-row") == Employee.objects.filter(active=True, can_install=True).count()
    assert login(roles.READER, "abl").get(reverse("planning:free_days")).status_code == 403


# --- weekly hours ------------------------------------------------------------------------

def test_weekly_hours_only_for_admin(demo):
    kaiser = Employee.objects.get(short_name="Kaiser")
    url = reverse("planning:person", args=[kaiser.pk])
    assert "pp-weeks" not in login(roles.DISPATCHER, "dispo").get(url).content.decode()
    assert "pp-weeks" not in login(roles.MANAGEMENT, "chef").get(url).content.decode()
    admin_html = login(roles.ADMIN, "admin").get(url).content.decode()
    assert "pp-weeks" in admin_html and "KW" in admin_html
    # daily hours stay visible for everybody who sees the plans
    assert "Std" in login(roles.DISPATCHER, "dispo2").get(url).content.decode()
