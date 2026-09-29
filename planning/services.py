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
from django.db.models import F, Q
from django.utils import timezone

from buildings.models import Building, InstallationOrder, OrderStatus
from conflicts.rules import Finding, OtherPlan, PlannedBuilding, planning_findings
from conflicts.services import installation_findings
from conflicts.services import refresh_for as refresh_conflicts_for
from documents.services import refresh_deadline

from . import geocoding
from .models import Absence, DriveSource, Employee, RoutingSource, StopKind, Tour, TourStatus, TourStop
from .rules.drive_time import estimate_drive_minutes, planned_drive_minutes
from .rules.ordering import order_stops
from .rules.working_time import confirmation_problems, schedule_day
from .tomtom import TomTomError, get_client, local_datetime

SELECTION_KEY = "plan_selection"
DRAFT_KEY = "plan_draft"
ORDER_SELECTION_KEY = "order_selection"


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


# The same for installation orders (checkboxes in the Montageaufträge list)

def get_order_selection(session):
    return list(session.get(ORDER_SELECTION_KEY, []))


def toggle_order_selection(session, order_id, selected):
    ids = [i for i in get_order_selection(session) if i != order_id]
    if selected:
        ids.append(order_id)
    session[ORDER_SELECTION_KEY] = ids
    return ids


def clear_order_selection(session):
    session[ORDER_SELECTION_KEY] = []


def order_bar_context(session):
    """Numbers for the "Montage planen (n)" button."""
    ids = get_order_selection(session)
    minutes = sum(o.duration_minutes for o in InstallationOrder.objects.filter(pk__in=ids))
    return {"selected_count": len(ids), "selected_minutes": minutes, "other_count": len(get_selection(session))}


def plan_bar_context(session):
    """Numbers for the "Fahrplan erstellen (n)" button in the list header."""
    ids = get_selection(session)
    minutes = sum(b.reading_minutes for b in Building.objects.filter(pk__in=ids))
    # ticked orders (🔧 Montage) go into the same plan
    return {"selected_count": len(ids), "selected_minutes": minutes, "other_count": len(get_order_selection(session))}


# =============================================================================
# Draft
# =============================================================================

def _stop_key(stop):
    return (stop["kind"], stop.get("building"), stop.get("order"))


def create_draft(building_ids, employee, date, start, break_minutes, strategy, order_ids=()):
    """New draft for one person and day. Existing stops of that day stay in it.

    building_ids become readings, order_ids installations (Montage).
    """
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
    for order in InstallationOrder.objects.filter(pk__in=order_ids).order_by("re_number"):
        new = {"kind": StopKind.INSTALLATION, "order": order.pk, "building": order.building_id}
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


def draft_from_tour(tour, date=None, employee=None):
    """Draft to recalculate or move an existing tour (calendar drag & drop).

    Nothing changes until the draft is confirmed or saved; the old tour stays
    where it is meanwhile.
    """
    date = date or tour.date
    employee = employee or tour.employee
    if (employee.pk, date) != (tour.employee_id, tour.date) and Tour.objects.filter(employee=employee, date=date).exists():
        raise ValueError(f"{employee} hat am {date:%d.%m.%Y} schon einen Fahrplan. Bitte dort ergänzen oder zuerst diesen Tag leeren.")
    stops = []
    for stop in tour.stops.order_by("position"):
        if stop.kind == StopKind.READING:
            stops.append({"kind": StopKind.READING, "building": stop.building_id})
        else:
            stops.append({"kind": StopKind.INSTALLATION, "order": stop.installation_order_id, "building": stop.building_id})
    moved = (employee.pk, date) != (tour.employee_id, tour.date)
    return {
        "employee": employee.pk,
        "date": date.isoformat(),
        "start": tour.start_time.strftime("%H:%M"),
        "break": tour.break_minutes or 30,
        "strategy": "far",
        "tour_id": tour.pk,
        "tour_version": tour.version,
        "moved_from": f"{tour.employee} am {tour.date:%d.%m.%Y}" if moved else "",
        "stops": stops,
    }


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


def add_stop(draft, kind, pk):
    """Add a reading (building pk) or an installation (order pk) at the end of the draft.

    Returns a short text for the toast, or raises ValueError (unknown / already in the plan).
    """
    if kind == StopKind.READING:
        building = Building.objects.filter(pk=pk).first()
        if building is None:
            raise ValueError("Liegenschaft nicht gefunden.")
        new, label = {"kind": StopKind.READING, "building": building.pk}, f"Ablesung {building.file_number} {building.street}"
    elif kind == StopKind.INSTALLATION:
        order = InstallationOrder.objects.filter(pk=pk).first()
        if order is None:
            raise ValueError("Montageauftrag nicht gefunden.")
        if order.status == OrderStatus.DONE:
            raise ValueError(f"{order.re_number} ist schon erledigt.")
        new, label = {"kind": StopKind.INSTALLATION, "order": order.pk, "building": order.building_id}, f"Montage {order.re_number} {order.street}"
    else:
        raise ValueError("Unbekannte Art.")
    if _stop_key(new) in {_stop_key(s) for s in draft["stops"]}:
        raise ValueError(f"{label} ist schon im Plan.")
    draft["stops"].append(new)
    return f"{label} hinzugefügt"


@dataclass
class Suggestion:
    """Something that would fit into the plan (shown under the stops with a "+" button)."""

    kind: str
    pk: int
    title: str
    reason: str


def draft_suggestions(draft, limit=6):
    """Readings and installations that belong together (prototype: "Zusammen mit der HA").

    - an open order for a building that is read in this plan
    - the reading of a building that gets an installation in this plan, if not planned yet
    - open orders assigned to this installer, still without an appointment
    """
    in_plan = {_stop_key(s) for s in draft["stops"]}
    building_ids = {s["building"] for s in draft["stops"] if s["kind"] == StopKind.READING}
    order_ids = {s["order"] for s in draft["stops"] if s["kind"] == StopKind.INSTALLATION}
    buildings = Building.objects.filter(pk__in=building_ids)
    cores = {b.file_number_core for b in buildings}
    open_orders = InstallationOrder.objects.exclude(status=OrderStatus.DONE).exclude(pk__in=order_ids)
    found = []
    for order in open_orders.filter(Q(building__in=building_ids) | Q(building_file_number_core__in=cores)).order_by("re_number"):
        found.append(Suggestion(StopKind.INSTALLATION, order.pk, f"🔧 Montage {order.re_number} · {order.street}, {order.city}",
                                f"offen für eine Liegenschaft dieses Plans · {order.duration_minutes} min"))
    for order in InstallationOrder.objects.filter(pk__in=order_ids, building__isnull=False).select_related("building"):
        key = (StopKind.READING, order.building_id, None)
        if key not in in_plan and not order.building.tour_stops.filter(kind=StopKind.READING).exists():
            b = order.building
            found.append(Suggestion(StopKind.READING, b.pk, f"📖 Ablesung {b.file_number} · {b.street}, {b.city}",
                                    f"gleiche Liegenschaft wie {order.re_number}, noch kein Ablesetermin · {b.reading_minutes} min"))
    employee = Employee.objects.filter(pk=draft["employee"]).first()
    if employee and employee.can_install:
        for order in (open_orders.filter(assigned_installers=employee, tour_stops__isnull=True)
                      .exclude(pk__in=[f.pk for f in found if f.kind == StopKind.INSTALLATION]).order_by("re_number")[:limit]):
            found.append(Suggestion(StopKind.INSTALLATION, order.pk, f"🔧 Montage {order.re_number} · {order.street}, {order.city}",
                                    f"{employee} zugewiesen, noch ohne Termin · {order.duration_minutes} min"))
    return found[:limit]


def search_targets(query, draft, limit=6):
    """Search box "+ Stopp hinzufügen": buildings (reading) and orders (installation)."""
    words = query.split()
    if not words or len(query.strip()) < 2:
        return []
    in_plan = {_stop_key(s) for s in draft["stops"]}
    buildings, orders = Building.objects.all(), InstallationOrder.objects.exclude(status=OrderStatus.DONE)
    for word in words:
        buildings = buildings.filter(Q(file_number__icontains=word) | Q(street__icontains=word) | Q(city__icontains=word)
                                     | Q(zip_code__startswith=word))
        orders = orders.filter(Q(re_number__icontains=word) | Q(building_file_number__icontains=word)
                               | Q(street__icontains=word) | Q(city__icontains=word) | Q(zip_code__startswith=word))
    results = [Suggestion(StopKind.READING, b.pk, f"📖 Ablesung {b.file_number} · {b.street}, {b.zip_code} {b.city}",
                          f"{b.reading_minutes} min")
               for b in buildings.order_by("file_number")[:limit] if (StopKind.READING, b.pk, None) not in in_plan]
    results += [Suggestion(StopKind.INSTALLATION, o.pk, f"🔧 Montage {o.re_number} · {o.street}, {o.zip_code} {o.city}",
                           f"{o.duration_minutes} min{' · ' + o.summary if o.summary else ''}")
                for o in orders.order_by("re_number")[:limit]
                if (StopKind.INSTALLATION, o.building_id, o.pk) not in in_plan]
    return results


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
    drive_reason: str = ""  # why this drive is only estimated
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
    def reading_minutes(self):
        return sum(s.work_minutes for s in self.stops if s.kind == StopKind.READING)

    @property
    def installation_minutes(self):
        return sum(s.work_minutes for s in self.stops if s.kind == StopKind.INSTALLATION)

    @property
    def ends_next_day(self):
        """The calculated end is after midnight (only with far too long days)."""
        return bool(self.day_plan.end and self.day_plan.end < self.start)

    @property
    def estimated_count(self):
        return sum(1 for s in self.stops[:-1] if s.drive_source != DriveSource.TOMTOM)

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


def _drive(client, origin, destination, departure, stop, next_stop=None):
    """Fill the drive fields of `stop` (TomTom if possible, else estimate + reason)."""
    reason = ""
    if client is None:
        reason = "kein TomTom-Schlüssel geladen"
    elif not origin or not destination:
        reason = "Adresse ohne Position"
    elif stop.point_source != "tomtom" or (next_stop is not None and next_stop.point_source != "tomtom"):
        reason = "Adresse bei TomTom nicht gefunden – Position nur geschätzt (PLZ-Mitte)"
    else:
        try:
            leg = client.route(origin, destination, departure)
        except TomTomError as error:
            reason = f"TomTom-Fehler: {error}"
        else:
            stop.drive_seconds = leg.seconds
            stop.drive_minutes = planned_drive_minutes(leg.seconds, leg.meters)
            stop.drive_km = Decimal(round(leg.meters / 1000, 1)).quantize(Decimal("0.1"))
            stop.drive_source = DriveSource.TOMTOM
            stop.drive_reason = ""
            stop.points = leg.points
            stop.warnings = stop.warnings + [w for w in leg.warnings if w not in stop.warnings]
            return
    stop.drive_seconds = None
    stop.drive_minutes = estimate_drive_minutes(origin, destination)
    stop.drive_km = None
    stop.drive_source = DriveSource.ESTIMATE
    stop.drive_reason = reason


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
                _drive(None, stop.point, stops[i + 1].point, None, stop, stops[i + 1])
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
            _drive(client, stop.point, stops[i + 1].point, t, stop, stops[i + 1])
        t += datetime.timedelta(minutes=stop.drive_minutes)


def _stops_in_other_tours(preview, draft, buildings):
    """Reading stops of these buildings in OTHER tours (not the day being planned,
    and not the tour that is being moved)."""
    stops = (TourStop.objects.filter(kind=StopKind.READING, building__in=buildings)
             .exclude(tour__employee=preview.employee, tour__date=preview.date))
    if draft.get("tour_id"):
        stops = stops.exclude(tour_id=draft["tour_id"])
    return stops.select_related("tour__employee")


def _add_findings(preview, draft):
    """Conflicts of every reading stop (conflicts/rules.py)."""
    reading = [s for s in preview.stops if s.kind == StopKind.READING]
    others = _stops_in_other_tours(preview, draft, [s.building for s in reading])
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

    # Installation stops: all section-8 rules as if the order were done on this day
    installing = [s for s in preview.stops if s.kind == StopKind.INSTALLATION]
    what_if = installation_findings([s.order.pk for s in installing], preview.date, preview.employee.short_name)
    for stop in installing:
        stop.findings = [Finding(severity, text) for severity, text in what_if.get(stop.order.pk, [])]
        if absent:
            stop.findings.append(Finding("critical", "Mitarbeiter ist an diesem Tag abwesend"))

    # The person should be able to do the job (Employee: "Ableser" / "Monteur" ticked)
    for stop in preview.stops:
        if stop.kind == StopKind.READING and not preview.employee.can_read:
            stop.findings.append(Finding("warning", f"{preview.employee} ist nicht als Ableser eingetragen"))
        if stop.kind == StopKind.INSTALLATION and not preview.employee.can_install:
            stop.findings.append(Finding("warning", f"{preview.employee} ist nicht als Monteur eingetragen"))


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

    _move_buildings_out_of_other_tours(preview, draft)

    plan = preview.day_plan
    tour.employee, tour.date = preview.employee, preview.date  # a moved tour gets its new day
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
    # A planned order is "Verplant" now (the office can still change the status by hand)
    for order in InstallationOrder.objects.filter(pk__in=[s.order.pk for s in preview.stops if s.order],
                                                  status__in=[OrderStatus.OPEN, OrderStatus.WORK_CARD]):
        order.status = OrderStatus.PLANNED
        order.save()  # save() (not update) so the change history records it
    # new dates -> check reading vs. installation again (conflicts/services.py)
    refresh_conflicts_for([s.building.pk for s in preview.stops if s.building], [s.order.pk for s in preview.stops if s.order])
    return tour


def _lock_tour(draft):
    """Optimistic locking: only save if nobody changed the tour since the draft was made."""
    if draft["tour_id"]:
        updated = Tour.objects.filter(pk=draft["tour_id"], version=draft["tour_version"]).update(version=F("version") + 1)
        if not updated:
            raise ConcurrentChange("Der Fahrplan wurde inzwischen von jemand anderem geändert.")
        tour = Tour.objects.get(pk=draft["tour_id"])
        target_taken = (Tour.objects.filter(employee_id=draft["employee"], date=draft["date"]).exclude(pk=tour.pk).exists())
        if target_taken:
            raise ConcurrentChange("Für den neuen Tag gibt es inzwischen schon einen Fahrplan.")
        return tour
    if Tour.objects.filter(employee_id=draft["employee"], date=draft["date"]).exists():
        raise ConcurrentChange("Für diesen Tag hat inzwischen jemand anderes einen Fahrplan angelegt.")
    return None


def _move_buildings_out_of_other_tours(preview, draft):
    """A building can only be in one tour: take it out of the others (prototype behaviour)."""
    buildings = [s.building for s in preview.stops if s.kind == StopKind.READING]
    old_stops = _stops_in_other_tours(preview, draft, buildings)
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


def delete_tour(tour):
    """Delete a tour; its buildings become 'unplanned' again (kept in the history)."""
    stops = list(tour.stops.all())
    buildings = [stop.building for stop in stops if stop.building]
    order_ids = [stop.installation_order_id for stop in stops if stop.installation_order_id]
    tour.delete()
    for building in buildings:
        refresh_deadline(building)
    # orders without any appointment left are open again
    for order in InstallationOrder.objects.filter(pk__in=order_ids, status=OrderStatus.PLANNED, tour_stops__isnull=True):
        order.status = OrderStatus.OPEN
        order.save()
    refresh_conflicts_for([b.pk for b in buildings], order_ids)
