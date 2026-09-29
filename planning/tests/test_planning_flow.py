"""Tour planning: draft -> preview -> save, with and without TomTom."""

import datetime
import io
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from buildings.models import Building
from core import roles
from documents.models import CostDocumentReceipt
from planning import services
from planning.models import Employee, RoutingSource, Tour, TourStatus
from planning.tomtom import GeocodeResult, Leg

pytestmark = pytest.mark.django_db
DAY = datetime.date(2027, 3, 1)  # a Monday without any tour in the demo data


class FakeTomTom:
    """Answers like TomTom: every drive takes 12:01 min / 9 km."""

    def __init__(self):
        self.routes = []

    def geocode(self, address):
        return GeocodeResult(48.7 + len(address) / 1000, 9.0 + len(address) / 1000, address, "Point Address", "")

    def route(self, origin, destination, departure):
        self.routes.append(departure)
        return Leg(seconds=721, meters=9000, warnings=[], points=[[origin[1], origin[0]], [destination[1], destination[0]]])

    def best_order(self, points):
        return list(range(len(points) - 2))


@pytest.fixture
def demo(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: datetime.date(2026, 9, 28))
    return Employee.objects.get(short_name="Keller")


@pytest.fixture
def tomtom(monkeypatch):
    fake = FakeTomTom()
    monkeypatch.setattr(services, "get_client", lambda: fake)
    return fake


@pytest.fixture
def no_tomtom(monkeypatch):
    monkeypatch.setattr(services, "get_client", lambda: None)


def unplanned(count):
    """Buildings in Calw without a tour yet."""
    return list(Building.objects.filter(region="Region Calw", tour_stops__isnull=True).order_by("file_number")[:count])


# stands for the planner who clicks "Arbeitszeit so übernehmen" (the test days are short)
PLANNER = SimpleNamespace(pk=None, get_username=lambda: "planer")


def draft_for(employee, buildings, start=datetime.time(8, 0), approve_time=True):
    draft = services.create_draft([b.pk for b in buildings], employee, DAY, start, 30, "far")
    return services.approve_time(draft, PLANNER) if approve_time else draft


def test_preview_without_tomtom_is_estimated_and_cannot_be_confirmed(demo, no_tomtom):
    preview = services.calculate_preview(draft_for(demo, unplanned(3)))
    assert [s.drive_source for s in preview.stops[:-1]] == ["estimate", "estimate"]
    assert not preview.can_confirm
    assert "TomTom" in preview.problems[0]


def test_preview_with_tomtom_rounds_drive_times_and_uses_departure_times(demo, tomtom):
    buildings = unplanned(3)
    preview = services.calculate_preview(draft_for(demo, buildings))
    assert [s.drive_minutes for s in preview.stops[:-1]] == [15, 15]  # 12:01 -> 13 -> 15
    first = preview.stops[0]
    assert tomtom.routes[0] == timezone.make_aware(datetime.datetime.combine(DAY, first.end))  # leaves when stop 1 ends
    assert preview.can_confirm and preview.day_plan.net_minutes == sum(b.reading_minutes for b in buildings) + 30


def test_confirm_saves_tour_and_starts_new_deadline(demo, tomtom):
    user = User.objects.create_user(username="dispo")
    buildings = unplanned(2)
    CostDocumentReceipt.objects.create(building=buildings[0], received_on=datetime.date(2026, 9, 1),
                                       deadline_start=datetime.date(2026, 9, 1), last_seen_status=buildings[0].status)
    tour = services.save_draft(draft_for(demo, buildings), user, confirm=True)
    assert (tour.status, tour.routing_source, tour.confirmed_by) == (TourStatus.CONFIRMED, RoutingSource.TOMTOM, user)
    assert tour.stops.count() == 2 and tour.stops.first().drive_to_next_minutes == 15
    receipt = CostDocumentReceipt.objects.get(building=buildings[0])
    assert receipt.reset_reason == "appointment"  # new appointment -> new 14-day deadline
    buildings[0].refresh_from_db()
    assert buildings[0].assigned_reader == demo


def test_confirm_without_tomtom_is_refused_but_provisional_works(demo, no_tomtom):
    draft = draft_for(demo, unplanned(2))
    with pytest.raises(ValueError, match="TomTom"):
        services.save_draft(draft, None, confirm=True)
    tour = services.save_draft(draft, None, confirm=False)
    assert (tour.status, tour.routing_source) == (TourStatus.PROVISIONAL, RoutingSource.NONE)


def test_building_from_another_tour_is_moved_and_old_tour_marked(demo, tomtom):
    other = Tour.objects.filter(stops__kind="reading").exclude(employee=demo).first()
    moved = other.stops.filter(kind="reading").first().building
    count_before = other.stops.count()
    preview = services.calculate_preview(draft_for(demo, [moved]))
    assert preview.stops[0].findings[0].moves_stop
    services.save_draft(draft_for(demo, [moved]), None, confirm=True)
    if count_before > 1:
        other.refresh_from_db()
        assert other.needs_recalculation and "verschoben" in other.change_reason
    assert moved.tour_stops.filter(kind="reading").count() == 1  # a reading is only in one tour


def test_optimistic_locking(demo, tomtom):
    buildings = unplanned(2)
    services.save_draft(draft_for(demo, buildings[:1]), None, confirm=True)
    # Two dispatchers open the same day ...
    draft_a = draft_for(demo, buildings[1:])
    draft_b = draft_for(demo, buildings[1:])
    # ... A saves first, so B's version is out of date:
    services.save_draft(draft_a, None, confirm=True)
    with pytest.raises(services.ConcurrentChange):
        services.save_draft(draft_b, None, confirm=True)


def test_reorder_and_remove(demo, no_tomtom):
    draft = draft_for(demo, unplanned(3))
    first, second = draft["stops"][0], draft["stops"][1]
    services.move_stop(draft, 0, +1)
    assert draft["stops"][:2] == [second, first]
    services.remove_stop(draft, 0)
    assert len(draft["stops"]) == 2


def test_planning_pages(client, demo, no_tomtom):
    user = User.objects.create_user(username="d", password="x")
    user.groups.add(Group.objects.get(name=roles.DISPATCHER))
    client.force_login(user)
    buildings = unplanned(2)
    for b in buildings:
        bar = client.post(reverse("planning:select"), {"building": b.pk, "checked": "on"})
    assert "(2)" in bar.content.decode()
    response = client.post(reverse("planning:dialog"), {"employee": demo.pk, "date": DAY.isoformat(), "start": "08:00",
                                                         "break_minutes": 30, "strategy": "far"})
    assert response["HX-Redirect"] == reverse("planning:draft")
    page = client.get(reverse("planning:draft")).content.decode()
    assert "Fahrplan prüfen" in page and "geschätzt" in page
    client.post(reverse("planning:draft_action"), {"action": "down", "index": 0})
    response = client.post(reverse("planning:draft_save"), {"confirm": "0"})
    assert response.status_code == 302
    assert Tour.objects.get(employee=demo, date=DAY).status == TourStatus.PROVISIONAL


def test_management_cannot_plan(client, demo):
    user = User.objects.create_user(username="l", password="x")
    user.groups.add(Group.objects.get(name=roles.MANAGEMENT))
    client.force_login(user)
    assert client.get(reverse("planning:dialog")).status_code == 403


def test_old_tour_keeps_history_when_a_stop_is_moved_out(demo, tomtom):
    """Regression: moving a stop out of a tour with several stops must not break the history."""
    other = next(t for t in Tour.objects.exclude(employee=demo) if t.stops.filter(kind="reading").count() > 1)
    moved = other.stops.filter(kind="reading").first().building
    version = other.version
    services.save_draft(draft_for(demo, [moved]), None, confirm=True)
    other.refresh_from_db()
    assert other.version == version + 1 and other.needs_recalculation
    assert other.history.first().needs_recalculation


def test_route_sketch_uses_dots_not_german_commas(client, demo, no_tomtom):
    user = User.objects.create_user(username="d2", password="x")
    user.groups.add(Group.objects.get(name=roles.DISPATCHER))
    client.force_login(user)
    session = client.session
    session[services.DRAFT_KEY] = draft_for(demo, unplanned(3))
    session.save()
    html = client.get(reverse("planning:draft")).content.decode()
    circles = [part.split('"')[1] for part in html.split('cx=')[1:]]
    assert circles and all("," not in value for value in circles)


class PartlyFailingTomTom(FakeTomTom):
    """Does not find one address and fails for one route, everything else works."""

    def __init__(self, missing_street, failing_after=None):
        super().__init__()
        self.missing_street, self.failing_after = missing_street, failing_after

    def geocode(self, address):
        from planning.tomtom import TomTomError
        if address.startswith(self.missing_street):
            raise TomTomError(f"TomTom findet die Adresse nicht: {address}")
        return super().geocode(address)


def test_only_the_affected_drives_are_estimated_and_the_reason_is_shown(demo, monkeypatch):
    buildings = unplanned(4)
    draft = draft_for(demo, buildings)
    missing = Building.objects.get(pk=draft["stops"][1]["building"])  # the 2nd stop in driving order
    monkeypatch.setattr(services, "get_client", lambda: PartlyFailingTomTom(missing.street))
    preview = services.calculate_preview(draft)
    sources = [s.drive_source for s in preview.stops[:-1]]
    # drive 1->2 and 2->3 touch the unknown address, 3->4 is still calculated by TomTom
    assert sources == ["estimate", "estimate", "tomtom"]
    assert "nicht gefunden" in preview.stops[0].drive_reason
    assert preview.estimated_count == 2 and not preview.can_confirm


def test_a_failing_route_does_not_stop_the_other_drives(demo, monkeypatch):
    from planning.tomtom import TomTomError

    class OneRouteFails(FakeTomTom):
        def route(self, origin, destination, departure):
            if not self.routes:
                self.routes.append(departure)
                raise TomTomError("TomTom-Fehler HTTP 400: Invalid request")
            return super().route(origin, destination, departure)

    monkeypatch.setattr(services, "get_client", lambda: OneRouteFails())
    preview = services.calculate_preview(draft_for(demo, unplanned(3)))
    assert [s.drive_source for s in preview.stops[:-1]] == ["estimate", "tomtom"]
    assert preview.stops[0].drive_reason == "TomTom-Fehler: TomTom-Fehler HTTP 400: Invalid request"


def test_demo_day_gives_the_demo_reader_a_tour(demo, no_tomtom):
    call_command("create_demo_users", password="LongDemoPassword1", stdout=io.StringIO())
    out = io.StringIO()
    call_command("demo_day", stdout=out)
    tour = Tour.objects.get(employee__short_name="Demo-Ableser", date=datetime.date(2026, 9, 28))
    assert tour.stops.count() == 5 and tour.status == TourStatus.PROVISIONAL and "angelegt" in out.getvalue()
    call_command("demo_day", stdout=out)  # second call: nothing new
    assert "schon einen Fahrplan" in out.getvalue()
