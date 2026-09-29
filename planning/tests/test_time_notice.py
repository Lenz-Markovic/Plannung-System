"""The last question "Bist du sicher?" before a plan is created - with options for a day over 7,5 h / under 6 h."""

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
from planning.models import Employee, StopKind, Tour
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


def reader():
    return Employee.objects.filter(can_read=True, can_install=False).exclude(tours__date=DAY).first()


def start_plan(client, buildings):
    person = reader()
    for building in buildings:
        client.post(reverse("planning:select"), {"building": building.pk, "checked": "on"})
    client.post(reverse("planning:dialog"), {"employee": person.pk, "date": DAY.isoformat(), "start": "08:00",
                                             "break_minutes": 30, "strategy": "far"})
    return person


def unplanned():
    return Building.objects.filter(region="Region Calw", tour_stops__isnull=True).order_by("file_number")


def test_short_day_asks_first_and_offers_unplanned_stops_nearby(planner):
    person = start_plan(planner, unplanned()[:1])
    page = planner.get(reverse("planning:draft")).content.decode()
    assert "nicht ausgelastet" in page and reverse("planning:plan_confirm") in page
    assert 'hx-trigger="load"' in page  # the question pops up at once

    # saving without answering the question is refused
    planner.post(reverse("planning:draft_save"), {"confirm": "1"})
    assert not Tour.objects.filter(employee=person, date=DAY).exists()

    question = planner.get(reverse("planning:plan_confirm")).content.decode()
    assert "nicht ausgelastet – wirklich so?" in question and "noch nicht geplant, in der Nähe" in question
    assert "＋ dazu" in question and 'name="sure" value="1"' in question and "Fahrplan final erstellen" in question

    # "＋ dazu" adds a stop from the question and closes the dialog
    preview = services.calculate_preview(planner.session[services.DRAFT_KEY])
    candidate = services.time_options(planner.session[services.DRAFT_KEY], preview)["fill"][0]
    response = planner.post(reverse("planning:draft_action"), {"action": "add", "kind": candidate.kind, "pk": candidate.pk, "close_modal": "1"})
    assert b'<div id="modal" hx-swap-oob="true"></div>' in response.content
    assert len(planner.session[services.DRAFT_KEY]["stops"]) == 2

    # "Ja, trotzdem so" saves
    planner.post(reverse("planning:draft_save"), {"confirm": "1", "sure": "1"})
    assert Tour.objects.get(employee=person, date=DAY).status == "confirmed"


def test_fill_candidates_are_unplanned_fit_and_match_the_person(planner):
    start_plan(planner, unplanned()[:1])
    draft = planner.session[services.DRAFT_KEY]
    preview = services.calculate_preview(draft)
    fill = services.time_options(draft, preview)["fill"]
    room = 450 - preview.day_plan.net_minutes
    assert fill and all(c.need <= room for c in fill)
    assert all(c.kind == StopKind.READING for c in fill)  # a reader only gets readings
    assert all(not Building.objects.get(pk=c.pk).tour_stops.filter(kind=StopKind.READING).exists() for c in fill)
    kms = [c.km for c in fill if c.km is not None]
    assert kms == sorted(kms)  # nearest first


def test_long_day_offers_to_take_out_or_swap(planner):
    # a long day: many big buildings
    big = list(Building.objects.filter(tour_stops__isnull=True).order_by("-reading_minutes_calculated")[:6])
    start_plan(planner, big)
    draft = planner.session[services.DRAFT_KEY]
    preview = services.calculate_preview(draft)
    assert preview.time_notice.kind == "over"
    options = services.time_options(draft, preview)
    assert options["kind"] == "over" and options["trim"]
    question = planner.get(reverse("planning:plan_confirm")).content.decode()
    assert "✕ nur herausnehmen" in question and "Der Tag ist zu lang – wirklich so?" in question

    first = options["trim"][0]
    before = len(draft["stops"])
    planner.post(reverse("planning:draft_action"), {"action": "remove", "index": first.index, "close_modal": "1"})
    assert len(planner.session[services.DRAFT_KEY]["stops"]) == before - 1


def test_swap_keeps_the_position(planner):
    start_plan(planner, unplanned()[:3])
    draft = planner.session[services.DRAFT_KEY]
    other = unplanned()[5]
    message = services.swap_stop(draft, 1, StopKind.READING, other.pk)
    assert "statt des alten Stopps" in message and draft["stops"][1]["building"] == other.pk and len(draft["stops"]) == 3
    with pytest.raises(ValueError):
        services.swap_stop(draft, 0, StopKind.READING, other.pk)  # already in the plan


def test_normal_day_also_asks_but_without_suggestions(planner):
    person = start_plan(planner, unplanned()[:1])
    draft = planner.session[services.DRAFT_KEY]
    building = Building.objects.get(pk=draft["stops"][0]["building"])
    building.reading_minutes_manual = 400  # a normal day (6-7,5 h)
    building.save()
    page = planner.get(reverse("planning:draft")).content.decode()
    assert 'hx-trigger="load"' not in page  # no pop-up while planning
    question = planner.get(reverse("planning:plan_confirm")).content.decode()
    assert "Fahrplan wirklich so erstellen?" in question and "Vorschlag" not in question
    planner.post(reverse("planning:draft_save"), {"confirm": "1"})  # without "sure": not saved
    assert not Tour.objects.filter(employee=person, date=DAY).exists()
    planner.post(reverse("planning:draft_save"), {"confirm": "1", "sure": "1"})
    assert Tour.objects.get(employee=person, date=DAY).status == "confirmed"


def test_saved_plan_shows_the_info_in_calendar_and_side_panel(planner):
    person = start_plan(planner, unplanned()[:1])
    planner.post(reverse("planning:draft_save"), {"confirm": "1", "sure": "1"})
    tour = Tour.objects.get(employee=person, date=DAY)
    event = next(e for e in calendar_events(DAY, DAY + datetime.timedelta(days=1), Employee.objects.all(), True) if e.get("id") == tour.pk)
    assert "⏱" in event["title"] and "weniger als 6 h" in event["extendedProps"]["tooltip"]
    assert "(zur Info)" in planner.get(reverse("planning:tour_detail", args=[tour.pk])).content.decode()


def test_slightly_long_day_offers_swaps_that_fit(planner):
    first, second = unplanned()[:2]
    for building in (first, second):
        building.reading_minutes_manual = 240  # 2 × 4 h -> a bit over 7,5 h
        building.save()
    start_plan(planner, [first, second])
    draft = planner.session[services.DRAFT_KEY]
    preview = services.calculate_preview(draft)
    assert preview.time_notice.kind == "over"
    options = services.time_options(draft, preview)
    assert options["enough"] and options["trim"][0].swaps
    room = 450 - (preview.day_plan.net_minutes - options["trim"][0].saves)
    assert all(c.need <= room for c in options["trim"][0].swaps)
    question = planner.get(reverse("planning:plan_confirm")).content.decode()
    assert "einen Stopp tauschen oder herausnehmen" in question and "⇄" in question

    swap = options["trim"][0].swaps[0]
    planner.post(reverse("planning:draft_action"), {"action": "swap", "index": options["trim"][0].index,
                                                    "kind": swap.kind, "pk": swap.pk, "close_modal": "1"})
    after = services.calculate_preview(planner.session[services.DRAFT_KEY])
    assert after.day_plan.net_minutes < preview.day_plan.net_minutes


# --- "Selbst eingeben" in the question ---------------------------------------------------

def test_type_in_an_re_number_yourself_and_add_it(planner):
    from buildings.models import InstallationOrder, OrderStatus

    start_plan(planner, unplanned()[:1])
    order = InstallationOrder.objects.exclude(status=OrderStatus.DONE).order_by("re_number").first()
    question = planner.get(reverse("planning:plan_confirm")).content.decode()
    assert "Selbst eingeben" in question and reverse("planning:confirm_search") in question
    found = planner.get(reverse("planning:confirm_search"), {"q": order.re_number}).content.decode()
    assert f"Montage {order.re_number}" in found and "＋ dazu" in found
    assert "passt noch in den Tag" in found or "über 7,5 h" in found
    planner.post(reverse("planning:draft_action"), {"action": "add", "kind": "installation", "pk": order.pk, "close_modal": "1"})
    assert any(s.get("order") == order.pk for s in planner.session[services.DRAFT_KEY]["stops"])


def test_search_shows_already_planned_and_swaps_for_a_long_day(planner):
    first, second = unplanned()[:2]
    for building in (first, second):
        building.reading_minutes_manual = 240
        building.save()
    start_plan(planner, [first, second])
    planned = Building.objects.filter(tour_stops__kind=StopKind.READING).select_related().first()
    found = planner.get(reverse("planning:confirm_search"), {"q": planned.file_number}).content.decode()
    assert "schon geplant:" in found
    small = unplanned()[3]
    found = planner.get(reverse("planning:confirm_search"), {"q": small.file_number}).content.decode()
    assert "⇄ statt" in found  # swapping it in for one of the long stops makes the day fit


def test_search_needs_two_characters_and_says_when_nothing_is_found(planner):
    start_plan(planner, unplanned()[:1])
    assert planner.get(reverse("planning:confirm_search"), {"q": "x"}).content == b""
    assert "Nichts gefunden" in planner.get(reverse("planning:confirm_search"), {"q": "RE99999999"}).content.decode()


def test_search_by_liegenschaftsnummer_in_both_forms(planner):
    from buildings.models import InstallationOrder

    start_plan(planner, unplanned()[:1])
    order = InstallationOrder.objects.exclude(building_file_number="").exclude(status="done").first()
    core = order.building_file_number_core
    bfw_form = "07" + core.zfill(5)   # e.g. 70008792 (CEOS) -> 0708792 (BFW)
    found = planner.get(reverse("planning:confirm_search"), {"q": bfw_form}).content.decode()
    assert order.re_number in found  # the order refers to the CEOS number, found with the BFW number
    building = unplanned()[4]
    found = planner.get(reverse("planning:confirm_search"), {"q": building.file_number}).content.decode()
    assert f"Ablesung {building.file_number}" in found
