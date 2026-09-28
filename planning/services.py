"""
Tour planning: draft -> preview -> save.

The workflow is the same as in the prototype:
1. The dispatcher ticks buildings in the list (selection in the session).
2. "Fahrplan erstellen": person, day, start, break, strategy -> a DRAFT
   (kept in the session, nothing is saved yet).
3. Preview: order, times and driving times (TomTom with the departure time,
   or an estimate without key), conflicts, working-time rules. Stops can be
   moved or removed; every change recalculates the preview.
4. Save: "bestätigen" only with real TomTom times and within 7.5 h;
   otherwise only "vorläufig speichern".

The business decisions are pure functions in planning/rules/ and
conflicts/rules.py; this module loads data, calls TomTom and saves.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from buildings.models import Building, InstallationOrder
from conflicts.rules import OtherPlan, PlannedBuilding, planning_findings
from documents.services import refresh_deadline

from . import geocoding
from .models import Absence, DriveSource, Employee, RoutingSource, StopKind, Tour, TourStatus, TourStop
from .rules.drive_time import estimate_drive_minutes, planned_drive_minutes
from .rules.ordering import order_stops
from .rules.working_time import confirmation_problems, schedule_day
from .tomtom import TomTomError, get_client, local_datetime

SELECTION_KEY = "plan_selection"
DRAFT_KEY = "plan_draft"


class ConcurrentChange(Exception):
    """Someone else saved this tour in the meantime (optimistic locking)."""


# =============================================================================
# Selection of buildings (checkboxes in the list), stored in the session
# =============================================================================

def get_selection(session):
    return list(session.get(SELECTION_KEY, []))


def toggle_selection(session, building_id, selected):
    ids = [i for i in get_selection(session) if i != building_id]
    if selected:
        ids.append(building_id)
    session[SELECTION_KEY] = ids
    return ids


def clear_selection(session):
    session[SELECTION_KEY] = []


def plan_bar_context(session):
    """Numbers for the "Fahrplan erstellen (n)" button in the list header."""
    ids = get_selection(session)
    minutes = sum(b.reading_minutes for b in Building.objects.filter(pk__in=ids))
    return {"selected_count": len(ids), "selected_minutes": minutes}


# =============================================================================
# Draft
# =============================================================================

def _stop_key(stop):
    return (stop["kind"], stop.get("building"), stop.get("order"))


def create_draft(building_ids, employee, date, start, break_minutes, strategy):
    """New draft for one person and day. Existing stops of that day stay in it."""
    tour = Tour.objects.filter(employee=employee, date=date).first()
    stops = []
    if tour:
        for stop in tour.stops.order_by("position"):
            if stop.kind == StopKind.READING:
                stops.append({"kind": StopKind.READING, "building": stop.building_id})
            else:
                stops.append({"kind": StopKind.INSTALLATION, "order": stop.installation_order_id,
                              "building": stop.building_id})
    existing = {_stop_key(s) for s in stops}
    for building_id in building_ids:
        new = {"kind": StopKind.READING, "building": building_id}
        if _stop_key(new) not in existing:
            stops.append(new)
            existing.add(_stop_key(new))

    draft = {
        "employee": employee.pk,
        "date": date.isoformat(),
        "start": start.strftime("%H:%M"),
        "break": break_minutes,
        "strategy": strategy,
        "tour_id": tour.pk if tour else None,
        "tour_version": tour.version if tour else None,
        "stops": stops,
    }
    return sort_draft(draft)


def sort_draft(draft):
    """Put the stops in driving order using the chosen strategy."""
    client = get_client()
    targets = _load_targets(draft["stops"])
    positions = {}
    for stop in draft["stops"]:
        point, _, _ = geocoding.position(targets[_stop_key(stop)], client)
        positions[_stop_key(stop)] = point
    draft["stops"] = order_stops(draft["stops"], draft["strategy"], lambda s: positions[_stop_key(s)])
    if client and draft["strategy"] == "short" and len(draft["stops"]) >= 4 and all(positions.values()):
        try:
            # First and last stop stay; TomTom sorts the ones in between.
            order = client.best_order([positions[_stop_key(s)] for s in draft["stops"]])
            inner = draft["stops"][1:-1]
            if sorted(order) == list(range(len(inner))):
                draft["stops"] = [draft["stops"][0]] + [inner[i] for i in order] + [draft["stops"][-1]]
        except TomTomError:
            pass  # keep the nearest-neighbour order
    return draft


def move_stop(draft, index, direction):
    """direction -1 = up, +1 = down."""
    stops, other = draft["stops"], index + direction
    if 0 <= index < len(stops) and 0 <= other < len(stops):
        stops[index], stops[other] = stops[other], stops[index]
    return draft


def remove_stop(draft, index):
    if 0 <= index < len(draft["stops"]) and len(draft["stops"]) > 1:
        del draft["stops"][index]
    return draft


# =============================================================================
# Preview (calculation)
# =============================================================================

@dataclass
class PreviewStop:
    index: int
    kind: str
    building: Building | None
    order: InstallationOrder | None
    target: object  # the object with the address (building or order)
    work_minutes: int
    point: tuple | None
    point_source: str | None
    warnings: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    start: datetime.time | None = None
    end: datetime.time | None = None
    break_after: bool = False
    # drive to the NEXT stop
    departure: datetime.time | None = None
    drive_minutes: int | None = None
    drive_seconds: int | None = None
    drive_km: Decimal | None = None
    drive_source: str = DriveSource.NONE
    points: list = field(default_factory=list)


@dataclass
class Preview:
    employee: Employee
    date: datetime.date
    start: datetime.time
    stops: list
    day_plan: object
    problems: list
    error: str = ""
    distance_km: Decimal = Decimal("0")
    commute_minutes: int | None = None
    commute_km: Decimal | None = None
    has_tomtom: bool = False

    @property
    def all_from_tomtom(self):
        return all(s.drive_source == DriveSource.TOMTOM for s in self.stops[:-1])

    @property
    def can_confirm(self):
        return not self.problems

    @property
    def critical_count(self):
        return sum(1 for s in self.stops for f in s.findings if f.severity == "critical")


def _load_targets(stops):
    building_ids = {s["building"] for s in stops if s.get("building")}
    order_ids = {s["order"] for s in stops if s.get("order")}
    buildings = Building.objects.in_bulk(building_ids)
    orders = InstallationOrder.objects.in_bulk(order_ids)
    targets = {}
    for stop in stops:
        if stop["kind"] == StopKind.READING:
            targets[_stop_key(stop)] = buildings[stop["building"]]
        else:
            targets[_stop_key(stop)] = orders[stop["order"]]
    return targets


def _drive(client, origin, destination, departure, stop):
    """Fill the drive fields of `stop` (TomTom if possible, else estimate)."""
    if client and origin and destination and stop.point_source == "tomtom":
        leg = client.route(origin, destination, departure)
        stop.drive_seconds = leg.seconds
        stop.drive_minutes = planned_drive_minutes(leg.seconds, leg.meters)
        stop.drive_km = Decimal(round(leg.meters / 1000, 1)).quantize(Decimal("0.1"))
        stop.drive_source = DriveSource.TOMTOM
        stop.points = leg.points
        stop.warnings = stop.warnings + [w for w in leg.warnings if w not in stop.warnings]
    else:
        stop.drive_seconds = None
        stop.drive_minutes = estimate_drive_minutes(origin, destination)
        stop.drive_km = None
        stop.drive_source = DriveSource.ESTIMATE


def calculate_preview(draft):
    employee = Employee.objects.get(pk=draft["employee"])
    date = datetime.date.fromisoformat(draft["date"])
    start = datetime.time.fromisoformat(draft["start"])
    client = get_client()
    targets = _load_targets(draft["stops"])

    stops, error = [], ""
    for i, raw in enumerate(draft["stops"]):
        target = targets[_stop_key(raw)]
        is_reading = raw["kind"] == StopKind.READING
        building = target if is_reading else target.building
        point, source, warnings = geocoding.position(target, client)
        if source == "tomtom" and point is None:
            source = None
        stops.append(PreviewStop(
            index=i, kind=raw["kind"], building=building, order=None if is_reading else target, target=target,
            work_minutes=target.reading_minutes if is_reading else target.duration_minutes,
            point=point, point_source=source, warnings=warnings,
        ))

    # Driving times depend on the departure time, and the departure depends on
    # the break, which depends on the total time. So: first calculate without
    # a break, then - if a break is needed - again for the legs after it.
    work = [s.work_minutes for s in stops]
    try:
        _calculate_legs(client, stops, date, start, work, break_after=None, break_minutes=0)
        plan = schedule_day(start, work, [s.drive_minutes for s in stops[:-1]], draft["break"])
        if plan.break_after_index is not None:
            _calculate_legs(client, stops, date, start, work, plan.break_after_index, draft["break"])
    except TomTomError as tomtom_error:
        error = str(tomtom_error)
        for i, stop in enumerate(stops[:-1]):
            if stop.drive_minutes is None:
                _drive(None, stop.point, stops[i + 1].point, None, stop)
    plan = schedule_day(start, work, [s.drive_minutes for s in stops[:-1]], draft["break"])
    for stop, times in zip(stops, plan.stops):
        stop.start, stop.end, stop.departure, stop.break_after = times.start, times.end, times.departure, times.break_after

    preview = Preview(employee=employee, date=date, start=start, stops=stops, day_plan=plan, problems=[],
                      error=error, has_tomtom=client is not None)
    preview.distance_km = sum((s.drive_km or Decimal("0")) for s in stops)
    _add_findings(preview, draft)
    preview.problems = confirmation_problems(plan, preview.all_from_tomtom and not error)
    _add_commute(preview, client)
    return preview


def _calculate_legs(client, stops, date, start, work, break_after, break_minutes):
    """Sequentially calculate all legs; the departure of each leg is known only then."""
    t = local_datetime(date, start)
    for i, stop in enumerate(stops[:-1]):
        t += datetime.timedelta(minutes=work[i])
        if break_after is not None and i == break_after:
            t += datetime.timedelta(minutes=break_minutes)
        if break_after is None or i >= break_after or stop.drive_minutes is None:
            _drive(client, stop.point, stops[i + 1].point, t, stop)
        t += datetime.timedelta(minutes=stop.drive_minutes)


def _add_findings(preview, draft):
    """Conflicts of every reading stop (conflicts/rules.py)."""
    reading = [s for s in preview.stops if s.kind == StopKind.READING]
    others = (TourStop.objects.filter(kind=StopKind.READING, building__in=[s.building for s in reading])
              .exclude(tour__employee=preview.employee, tour__date=preview.date)
              .select_related("tour__employee"))
    other_plans = {}
    for stop in others:
        other_plans.setdefault(stop.building_id, []).append(
            OtherPlan(stop.tour.employee.short_name, stop.tour.date, stop.tour.status == TourStatus.PROVISIONAL))
    installations = {}
    for stop in (TourStop.objects.filter(kind=StopKind.INSTALLATION, installation_order__building__in=[s.building for s in reading])
                 .select_related("tour", "installation_order")):
        installations.setdefault(stop.installation_order.building_id, set()).add(stop.tour.date)

    absent = Absence.objects.filter(employee=preview.employee, start_date__lte=preview.date, end_date__gte=preview.date).exists()
    planned = [PlannedBuilding(key=s.index, file_number_core=s.building.file_number_core,
                               other_plans=tuple(other_plans.get(s.building.pk, [])),
                               installation_dates=tuple(sorted(installations.get(s.building.pk, []))))
               for s in reading]
    findings = planning_findings(planned, preview.date, absent=absent)
    for stop in reading:
        stop.findings = findings[stop.index]


def _add_commute(preview, client):
    """Drive from home to the first stop: shown, but not working time."""
    employee = preview.employee
    if not (client and employee.street and preview.stops and preview.stops[0].point_source == "tomtom"):
        return
    try:
        home, source, _ = geocoding.position(employee, client)
        if source == "tomtom":
            leg = client.route(home, preview.stops[0].point, local_datetime(preview.date, preview.start))
            preview.commute_minutes = planned_drive_minutes(leg.seconds, leg.meters)
            preview.commute_km = Decimal(round(leg.meters / 1000, 1)).quantize(Decimal("0.1"))
    except TomTomError:
        pass


# =============================================================================
# Saving
# =============================================================================

@transaction.atomic
def save_draft(draft, user, confirm):
    """Save the draft as a tour. confirm=False saves it as 'vorläufig'."""
    preview = calculate_preview(draft)
    if confirm and not preview.can_confirm:
        raise ValueError(" ".join(preview.problems))

    tour = _lock_tour(draft)
    if tour is None:
        tour = Tour(employee=preview.employee, date=preview.date, created_by=user)

    _move_buildings_out_of_other_tours(preview, user)

    plan = preview.day_plan
    tour.start_time = preview.start
    tour.status = TourStatus.CONFIRMED if confirm else TourStatus.PROVISIONAL
    tour.needs_recalculation, tour.change_reason = False, ""
    tour.routing_source = RoutingSource.TOMTOM if preview.all_from_tomtom and not preview.error else RoutingSource.NONE
    tour.routing_note = (f"TomTom, Pkw schnellste Route, Verkehr {preview.date:%d.%m.%Y} zur jeweiligen Abfahrtszeit"
                         if tour.routing_source == RoutingSource.TOMTOM else "vorläufig – Fahrzeiten geschätzt, noch nicht gültig")
    tour.routing_calculated_at = timezone.now()
    tour.work_minutes, tour.drive_minutes, tour.break_minutes = plan.work_minutes, plan.drive_minutes, plan.break_minutes
    tour.break_after_position = plan.break_after_index + 1 if plan.break_after_index is not None else None
    tour.end_time, tour.distance_km = plan.end, preview.distance_km
    tour.commute_to_minutes, tour.commute_to_km = preview.commute_minutes, preview.commute_km
    tour.confirmed_by, tour.confirmed_at = (user, timezone.now()) if confirm else (None, None)
    tour.save()

    tour.stops.all().delete()
    for position, stop in enumerate(preview.stops, start=1):
        TourStop.objects.create(
            tour=tour, position=position, kind=stop.kind, building=stop.building, installation_order=stop.order,
            start_time=stop.start, end_time=stop.end, work_minutes=stop.work_minutes,
            drive_to_next_seconds=stop.drive_seconds, drive_to_next_minutes=stop.drive_minutes,
            drive_to_next_km=stop.drive_km, departure_time=stop.departure,
            drive_source=stop.drive_source if position < len(preview.stops) else DriveSource.NONE,
            route_warnings=stop.warnings,
        )

    for stop in preview.stops:
        if stop.kind == StopKind.READING and stop.building.assigned_reader_id != preview.employee.pk:
            stop.building.assigned_reader = preview.employee
            stop.building.save()
        if stop.building:
            refresh_deadline(stop.building)  # new appointment -> new 14-day deadline
    return tour


def _lock_tour(draft):
    """Optimistic locking: only save if nobody changed the tour since the draft was made."""
    if draft["tour_id"]:
        updated = Tour.objects.filter(pk=draft["tour_id"], version=draft["tour_version"]).update(version=F("version") + 1)
        if not updated:
            raise ConcurrentChange("Der Fahrplan wurde inzwischen von jemand anderem geändert.")
        return Tour.objects.get(pk=draft["tour_id"])
    if Tour.objects.filter(employee_id=draft["employee"], date=draft["date"]).exists():
        raise ConcurrentChange("Für diesen Tag hat inzwischen jemand anderes einen Fahrplan angelegt.")
    return None


def _move_buildings_out_of_other_tours(preview, user):
    """A building can only be in one tour: take it out of the others (prototype behaviour)."""
    building_ids = [s.building.pk for s in preview.stops if s.kind == StopKind.READING]
    old_stops = (TourStop.objects.filter(kind=StopKind.READING, building_id__in=building_ids)
                 .exclude(tour__employee=preview.employee, tour__date=preview.date).select_related("tour"))
    changed_tours = {}
    for stop in old_stops:
        changed_tours.setdefault(stop.tour, 0)
        changed_tours[stop.tour] += 1
        stop.delete()
    for tour, count in changed_tours.items():
        remaining = list(tour.stops.order_by("position"))
        if not remaining:
            tour.delete()
            continue
        for position, stop in enumerate(remaining, start=1):
            if stop.position != position:
                stop.position = position
                stop.save(update_fields=["position"])
        tour.needs_recalculation = True
        tour.change_reason = (f"{count} Liegenschaft(en) in den Plan {preview.employee} "
                              f"{preview.date:%d.%m.%Y} verschoben – bitte neu mit TomTom rechnen")
        tour.version += 1  # counts as a change for optimistic locking
        tour.save()
