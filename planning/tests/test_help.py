"""🤝 Help at ONE object: a person with an own plan helps in another plan."""

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
from planning.models import Employee, StopKind, Tour, TourStop

pytestmark = pytest.mark.django_db
DAY = datetime.date(2026, 11, 17)


@pytest.fixture
def demo(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: datetime.date(2026, 9, 28))
    monkeypatch.setattr(services, "get_client", lambda: None)


def free(count):
    return [e for e in Employee.objects.filter(can_read=True).order_by("short_name")
            if not Tour.objects.filter(services.tours_with(e), date=DAY).exists()][:count]


def plan(person, buildings, team=()):
    draft = services.create_draft([b.pk for b in buildings], person, DAY, datetime.time(8), 30, "far")
    for mate in team:
        services.set_team(draft, add=mate.pk)
    return services.save_draft(draft, None, confirm=False)


def unplanned(count, skip=0):
    return list(Building.objects.filter(tour_stops__isnull=True).order_by("-reading_minutes_calculated")[skip:skip + count])


@pytest.fixture
def two_plans(demo):
    """Keller-like lead with a big object, and a helper with an own plan that day."""
    lead, helper = free(2)
    big, own = unplanned(1), unplanned(1, skip=1)
    return plan(lead, big), plan(helper, own), helper


def big_stop(tour):
    return tour.stops.get(kind=StopKind.READING)


def test_help_adds_a_stop_to_the_helpers_own_plan_and_splits_the_work(two_plans):
    helped, own, helper = two_plans
    stop = big_stop(helped)
    draft = services.help_draft(stop, helper)
    assert draft["tour_id"] == own.pk  # the helper keeps the own plan
    preview = services.calculate_preview(draft)
    help_stop = next(s for s in preview.stops if s.kind == StopKind.HELP)
    assert help_stop.help_tour == helped and help_stop.people == 2
    assert help_stop.work_minutes == services.team_minutes(stop.building.reading_minutes, 2)

    services.save_draft(draft, None, confirm=False)
    saved = own.stops.get(kind=StopKind.HELP)
    assert saved.help_tour == helped and saved.building == stop.building
    helped.refresh_from_db()
    assert helped.needs_recalculation and "Hilfe von" in helped.change_reason

    # the helped plan, recalculated: the helper counts, its work time at the object is split
    again = services.calculate_preview(services.draft_from_tour(helped))
    big = next(s for s in again.stops if s.kind == StopKind.READING)
    assert [h.tour.employee for h in big.helpers] == [helper] and big.people == 2
    assert big.work_minutes == services.team_minutes(stop.building.reading_minutes, 2)


def test_helper_without_own_plan_gets_a_new_one(demo):
    lead, helper = free(2)
    helped = plan(lead, unplanned(1))
    draft = services.help_draft(big_stop(helped), helper)
    assert draft["tour_id"] is None and draft["employee"] == helper.pk
    tour = services.save_draft(draft, None, confirm=False)
    assert [s.kind for s in tour.stops.all()] == [StopKind.HELP]


def test_help_is_refused_for_the_same_plan_and_for_team_members(demo):
    lead, mate, other = free(3)
    helped = plan(lead, unplanned(1), team=[mate])
    with pytest.raises(ValueError, match="schon in diesem Plan"):
        services.help_draft(big_stop(helped), mate)
    second = plan(other, unplanned(1, skip=1))
    with pytest.raises(ValueError, match="fest in einem Team"):
        services.help_draft(big_stop(second), mate)  # fixed in a team that day: cannot help elsewhere


def test_help_stop_warns_when_times_do_not_match(two_plans):
    helped, own, helper = two_plans
    draft = services.help_draft(big_stop(helped), helper)
    draft["start"] = "16:00"  # the helper would come in the late afternoon
    help_stop = next(s for s in services.calculate_preview(draft).stops if s.kind == StopKind.HELP)
    assert any("ist dort" in f.message and f.severity == "warning" for f in help_stop.findings)


def test_deleting_the_helped_plan(two_plans):
    helped, own, helper = two_plans
    services.save_draft(services.help_draft(big_stop(helped), helper), None, confirm=False)
    services.delete_tour(helped)
    own.refresh_from_db()
    assert own.needs_recalculation
    help_stop = next(s for s in services.calculate_preview(services.draft_from_tour(own)).stops if s.kind == StopKind.HELP)
    assert any("gelöscht" in f.message for f in help_stop.findings)


def test_side_panel_button_calendar_excel_and_mein_tag(two_plans):
    helped, own, helper = two_plans
    dispo_user = User.objects.create_user(username="dispo")
    dispo_user.groups.add(Group.objects.get(name=roles.DISPATCHER))
    dispo = Client()
    dispo.force_login(dispo_user)
    stop = big_stop(helped)
    panel = dispo.get(reverse("planning:tour_detail", args=[helped.pk])).content.decode()
    assert "🤝 + Helfer" in panel and reverse("planning:help_request", args=[stop.pk]) in panel
    response = dispo.post(reverse("planning:help_request", args=[stop.pk]), {"helper": helper.pk})
    assert response["HX-Redirect"] == reverse("planning:draft")
    assert "art-tag help" in dispo.get(reverse("planning:draft")).content.decode()
    dispo.post(reverse("planning:draft_save"), {"confirm": "0", "sure": "1"})  # "final erstellen" in the question "Bist du sicher?"

    assert f"🤝 Helfer: {helper}" in dispo.get(reverse("planning:tour_detail", args=[helped.pk])).content.decode()
    event = next(e for e in calendar_events(DAY, DAY + datetime.timedelta(days=1), Employee.objects.all(), True) if e.get("id") == own.pk)
    assert "🤝 1× Hilfe" in event["title"] and "🤝" in event["extendedProps"]["tooltip"]
    ws = build_workbook([own])[0].active
    assert any(c.value and f"🤝 Hilfe bei {helped.people_label}" in str(c.value) for c in ws["E"])

    reader_user = User.objects.create_user(username="helper")
    reader_user.groups.add(Group.objects.get(name=roles.READER))
    helper.user = reader_user
    helper.save()
    reader = Client()
    reader.force_login(reader_user)
    assert f"Hilfe bei {helped.people_label}" in reader.get(reverse("planning:my_day"), {"datum": DAY.isoformat()}).content.decode()


def test_team_list_offers_only_free_people(demo):
    lead, busy, free_one = free(3)
    plan(busy, unplanned(1))
    draft = services.create_draft([unplanned(1, skip=1)[0].pk], lead, DAY, datetime.time(8), 30, "far")
    from planning.views import _team_candidates
    candidates = list(_team_candidates(draft))
    assert free_one in candidates and busy not in candidates and lead not in candidates
