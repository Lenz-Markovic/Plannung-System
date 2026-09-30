"""🗓 Wochenplanung by hand: board, put on, move, take off, check, save as provisional plans."""

import datetime

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core import roles
from planning import services, week
from planning.models import Absence, Employee, StopKind, Tour, TourStatus

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2030, 3, 6)          # far after the demo plans: an empty week to plan
MONDAY = datetime.date(2030, 3, 4)


@pytest.fixture
def demo(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: TODAY)
    monkeypatch.setattr(services, "get_client", lambda: None)


def office():
    u = User.objects.create_user(username="dispo")
    u.groups.add(Group.objects.get(name=roles.DISPATCHER))
    c = Client()
    c.force_login(u)
    return c


def reader():
    return Employee.objects.filter(active=True, can_read=True).order_by("short_name").first()


def open_items(c, kind="reading", n=2):
    html = c.get(reverse("planning:week"), {"kw": MONDAY.isoformat(), "art": kind}).content.decode()
    return [part.split('"')[0] for part in html.split(f'name="item" value="')[1:n + 1]], html


def place(c, employee, date, items, **extra):
    return c.post(reverse("planning:week_place"), {"employee": employee.pk, "date": date.isoformat(), "item": items,
                                                   "kw": MONDAY.isoformat(), **extra})


def test_page_shows_the_week_the_people_and_the_open_list(demo):
    c = office()
    items, html = open_items(c)
    assert "🗓 Wochenplanung" in html and "Nichts wird automatisch verteilt" in html and "KW 10" in html
    assert "Noch nicht geplant" in html and len(items) == 2 and reader().short_name in html
    assert html.count('data-drop ') >= 5


def test_put_on_a_day_move_and_take_off(demo):
    c = office()
    items, _ = open_items(c)
    person = reader()
    answer = place(c, person, MONDAY, items).content.decode()
    assert "2 → " in answer and "≈" in answer and 'hx-swap-oob="true"' in answer
    days = week.get_days(c.session, MONDAY)
    assert len(days) == 1 and len(days[0]["stops"]) == 2 and days[0]["work"] > 0
    assert items[0] not in answer.split('id="week-pool"')[1]            # no longer in "Noch nicht geplant"

    tuesday = MONDAY + datetime.timedelta(days=1)
    place(c, person, tuesday, [items[0]])                               # moved, never twice
    days = week.get_days(c.session, MONDAY)
    assert [len(d["stops"]) for d in days] == [1, 1]
    c.post(reverse("planning:week_remove"), {"employee": person.pk, "date": tuesday.isoformat(), "item": items[0],
                                             "kw": MONDAY.isoformat()})
    assert len(week.get_days(c.session, MONDAY)) == 1
    c.post(reverse("planning:week_clear"), {"kw": MONDAY.isoformat()})
    assert week.get_days(c.session, MONDAY) == []


def test_refuses_absent_planned_wrong_kind_and_empty(demo):
    c = office()
    items, _ = open_items(c)
    person = reader()
    Absence.objects.create(employee=person, start_date=MONDAY, end_date=MONDAY)
    assert "abwesend" in place(c, person, MONDAY, items).content.decode()
    assert "nicht zur gewählten Woche" in place(c, person, MONDAY + datetime.timedelta(days=7), items).content.decode()
    assert "Erst links Objekte ankreuzen" in place(c, person, MONDAY + datetime.timedelta(days=1), []).content.decode()
    reader_only = Employee.objects.filter(active=True, can_read=True, can_install=False).first()
    orders, _ = open_items(c, "installation", 1)
    if reader_only and orders:
        assert "darf laut Stammdaten nicht montieren" in place(c, reader_only, MONDAY + datetime.timedelta(days=2),
                                                               orders).content.decode()
    wednesday = MONDAY + datetime.timedelta(days=2)
    services.save_draft(services.create_draft([int(items[0].split(":")[1])], person, wednesday, datetime.time(8), 30,
                                              "short"), None, confirm=False)
    assert "schon einen Fahrplan" in place(c, person, wednesday, items[1:]).content.decode()
    assert week.get_days(c.session, MONDAY) == []


def test_save_the_week_as_provisional_plans(demo):
    c = office()
    items, _ = open_items(c, n=3)
    person = reader()
    place(c, person, MONDAY, items[:2])
    place(c, person, MONDAY + datetime.timedelta(days=1), items[2:])
    response = c.post(reverse("planning:week_save"), {"kw": MONDAY.isoformat()})
    assert response["HX-Refresh"] == "true"
    tours = Tour.objects.filter(employee=person, date__gte=MONDAY, date__lte=MONDAY + datetime.timedelta(days=4))
    assert tours.count() == 2 and all(t.status == TourStatus.PROVISIONAL for t in tours)
    assert sum(t.stops.count() for t in tours) == 3
    assert week.get_days(c.session, MONDAY) == []
    html = c.get(reverse("planning:week"), {"kw": MONDAY.isoformat()}).content.decode()
    assert "📅 Plan" in html and items[0] not in html.split('id="week-pool"')[1]


def test_check_one_day_opens_the_draft(demo):
    c = office()
    items, _ = open_items(c)
    person = reader()
    place(c, person, MONDAY, items)
    response = c.post(reverse("planning:week_open"), {"employee": person.pk, "date": MONDAY.isoformat(), "kw": MONDAY.isoformat()})
    assert response["HX-Redirect"] == reverse("planning:draft")
    assert c.session[services.DRAFT_KEY]["stops"] and week.get_days(c.session, MONDAY) == []


def test_only_planners(demo):
    u = User.objects.create_user(username="lead")
    u.groups.add(Group.objects.get(name=roles.MANAGEMENT))
    c = Client()
    c.force_login(u)
    assert c.get(reverse("planning:week")).status_code == 403
    assert "🗓 Wochenplanung" in office().get(reverse("planning:calendar")).content.decode()
