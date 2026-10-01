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
from django.db.models import F, Prefetch, Q
from django.utils import timezone

from buildings.models import Building, BuildingStatus, InstallationOrder, OrderStatus
from buildings.rules.file_numbers import normalize_file_number
from conflicts.rules import Finding, OtherPlan, PlannedBuilding, planning_findings
from conflicts.services import installation_findings
from conflicts.services import refresh_for as refresh_conflicts_for
from documents.services import refresh_deadline

from . import geocoding
from .models import Absence, DriveSource, Employee, RoutingSource, StopKind, Tour, TourStatus, TourStop, Visit
from .queries import OPEN_STOP, current_first
from .rules import autoplan as autoplan_rules
from .rules.availability import HORIZON_DAYS, first_free_day
from .rules.drive_time import distance_km, estimate_drive_minutes, planned_drive_minutes
from .rules.ordering import order_stops
from .rules.working_time import (MAX_NET_MINUTES, confirmation_problems, fits_in_day, schedule_day, team_minutes,
                                 time_notice, trim_choices)
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
    key = (stop["kind"], stop.get("building"), stop.get("order"))
    return key + (stop.get("help_tour"),) if stop["kind"] == StopKind.HELP else key


def stop_dict(stop):
    """A saved TourStop as a draft stop."""
    if stop.kind == StopKind.READING:
        return {"kind": StopKind.READING, "building": stop.building_id}
    if stop.kind == StopKind.HELP:
        return {"kind": StopKind.HELP, "building": stop.building_id, "order": stop.installation_order_id,
                "help_tour": stop.help_tour_id}
    return {"kind": StopKind.INSTALLATION, "order": stop.installation_order_id, "building": stop.building_id}


def create_draft(building_ids, employee, date, start, break_minutes, strategy, order_ids=()):
    """New draft for one person and day. Existing stops of that day stay in it.

    building_ids become readings, order_ids installations (Montage).
    """
    tour = Tour.objects.filter(employee=employee, date=date).first()
    stops = []
    if tour:
        stops = [stop_dict(stop) for stop in tour.stops.order_by("position")]
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
        "team": [e.pk for e in tour.team.all()] if tour else [],
        "split": tour.split_work if tour else True,
    }
    return sort_draft(draft)


def is_visited(stop):
    """Reported from Mein Tag (done or an Ergebnis): the stop is history and stays where it is."""
    return stop.done_at is not None or bool(stop.outcome)


def _visited_here(draft):
    """Buildings already visited (reported) in the plan being saved - they never pull the object away from other plans."""
    if not draft.get("tour_id"):
        return set()
    return set(TourStop.objects.filter(tour_id=draft["tour_id"], kind=StopKind.READING)
               .filter(Q(done_at__isnull=False) | ~Q(outcome="")).values_list("building_id", flat=True))


def draft_from_tour(tour, date=None, employee=None):
    """Draft to recalculate or move an existing tour (calendar drag & drop).

    Nothing changes until the draft is confirmed or saved; the old tour stays
    where it is meanwhile.
    """
    date = date or tour.date
    employee = employee or tour.employee
    if (employee.pk, date) != (tour.employee_id, tour.date) and Tour.objects.filter(employee=employee, date=date).exists():
        raise ValueError(f"{employee} hat am {date:%d.%m.%Y} schon einen Fahrplan. Bitte dort ergänzen oder zuerst diesen Tag leeren.")
    moved = (employee.pk, date) != (tour.employee_id, tour.date)
    all_stops = list(tour.stops.order_by("position"))
    visited = [stop for stop in all_stops if is_visited(stop)]
    if moved and visited:
        # reported stops stay on their day as history - only the open ones move (a new plan on the new day)
        open_stops = [stop for stop in all_stops if not is_visited(stop)]
        if not open_stops:
            raise ValueError("Alle Stopps dieses Plans sind schon gemeldet – der Plan bleibt als Nachweis an seinem Tag.")
        return {
            "employee": employee.pk, "date": date.isoformat(), "start": tour.start_time.strftime("%H:%M"),
            "break": tour.break_minutes or 30, "strategy": "far", "tour_id": None, "tour_version": None,
            "split_from": tour.pk, "split_version": tour.version,
            "moved_from": f"{tour.employee} am {tour.date:%d.%m.%Y} (nur die {len(open_stops)} offenen Stopps; "
                          f"{len(visited)} gemeldete bleiben dort)",
            "stops": [stop_dict(stop) for stop in open_stops],
            "team": [e.pk for e in tour.team.all() if e.pk != employee.pk], "split": tour.split_work,
        }
    stops = [stop_dict(stop) for stop in all_stops]
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
        "team": [e.pk for e in tour.team.all() if e.pk != employee.pk],
        "split": tour.split_work,
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
    minutes: int = 0  # work time of this stop


SUGGESTION_DRIVE_MINUTES = 15  # rough extra drive per suggested stop


def visit_revisit_ids():
    """(building ids, order ids) that need a 🔁 Nachtermin (planning/visits.py)."""
    from .visits import revisit_ids
    return revisit_ids()


def _storno():
    """(building ids, order ids) with an open ⛔ Storno note: never suggested automatically."""
    from journal.notes import open_storno
    return open_storno()


def draft_suggestions(draft, limit=6, net_minutes=None):
    """Readings and installations that belong together (prototype: "Zusammen mit der HA").

    This is an AUTOMATIC choice of the system, so it keeps to 7,5 h strictly:
    with net_minutes (the plan so far) only stops that still fit are suggested.
    Returns (suggestions, number left out because the day would be too long).

    - an open order for a building that is read in this plan
    - the reading of a building that gets an installation in this plan, if not planned yet
    - open orders assigned to this installer, still without an appointment
    """
    in_plan = {_stop_key(s) for s in draft["stops"]}
    building_ids = {s["building"] for s in draft["stops"] if s["kind"] == StopKind.READING}
    order_ids = {s["order"] for s in draft["stops"] if s["kind"] == StopKind.INSTALLATION}
    buildings = Building.objects.filter(pk__in=building_ids)
    cores = {b.file_number_core for b in buildings}
    storno_buildings, storno_orders = _storno()
    open_orders = InstallationOrder.objects.exclude(status=OrderStatus.DONE).exclude(pk__in=order_ids | storno_orders)
    found = []
    for order in open_orders.filter(Q(building__in=building_ids) | Q(building_file_number_core__in=cores)).order_by("re_number"):
        found.append(Suggestion(StopKind.INSTALLATION, order.pk, f"🔧 Montage {order.re_number} · {order.street}, {order.city}",
                                f"offen für eine Liegenschaft dieses Plans · {order.duration_minutes} min", order.duration_minutes))
    for order in InstallationOrder.objects.filter(pk__in=order_ids, building__isnull=False).select_related("building"):
        key = (StopKind.READING, order.building_id, None)
        if (key not in in_plan and order.building_id not in storno_buildings
                and not order.building.tour_stops.filter(OPEN_STOP, kind=StopKind.READING).exists()
                and (order.building_id in visit_revisit_ids()[0]
                     or not order.building.tour_stops.filter(kind=StopKind.READING).exists())):
            b = order.building
            found.append(Suggestion(StopKind.READING, b.pk, f"📖 Ablesung {b.file_number} · {b.street}, {b.city}",
                                    f"gleiche Liegenschaft wie {order.re_number}, noch kein Ablesetermin · {b.reading_minutes} min", b.reading_minutes))
    employee = Employee.objects.filter(pk=draft["employee"]).first()
    if employee and employee.can_install:
        for order in (open_orders.filter(assigned_installers=employee, tour_stops__isnull=True)
                      .exclude(pk__in=[f.pk for f in found if f.kind == StopKind.INSTALLATION]).order_by("re_number")[:limit]):
            found.append(Suggestion(StopKind.INSTALLATION, order.pk, f"🔧 Montage {order.re_number} · {order.street}, {order.city}",
                                    f"{employee} zugewiesen, noch ohne Termin · {order.duration_minutes} min", order.duration_minutes))
    if net_minutes is None:
        return found[:limit], 0
    fitting, used = [], net_minutes
    for suggestion in found:
        extra = suggestion.minutes + SUGGESTION_DRIVE_MINUTES
        if fits_in_day(used, extra):
            fitting.append(suggestion)
            used += extra
    return fitting[:limit], len(found) - len(fitting)


def search_targets(query, draft, limit=6):
    """Search box "+ Stopp hinzufügen": buildings (reading) and orders (installation)."""
    words = query.split()
    if not words or len(query.strip()) < 2:
        return []
    in_plan = {_stop_key(s) for s in draft["stops"]}
    buildings, orders = Building.objects.all(), InstallationOrder.objects.exclude(status=OrderStatus.DONE)
    for word in words:
        # A Liegenschaftsnummer finds the building in both forms: BFW 0704806 = CEOS 70004806 (same "core")
        core = normalize_file_number(word) if word.isdigit() and len(word) >= 6 else ""
        buildings = buildings.filter(Q(file_number__icontains=word) | Q(street__icontains=word) | Q(city__icontains=word)
                                     | Q(zip_code__startswith=word) | (Q(file_number_core=core) if core else Q(pk__in=[])))
        orders = orders.filter(Q(re_number__icontains=word) | Q(building_file_number__icontains=word)
                               | Q(street__icontains=word) | Q(city__icontains=word) | Q(zip_code__startswith=word)
                               | (Q(building_file_number_core=core) if core else Q(pk__in=[])))
    def planned(stops):
        """' · schon geplant: Keller 03.12.2026' - saving moves a reading here; an order would get a 2nd date."""
        stop = min(stops, key=lambda st: st.tour.date, default=None)
        return f" · schon geplant: {stop.tour.employee} {stop.tour.date:%d.%m.%Y}" if stop else ""

    reading_stops = TourStop.objects.filter(kind=StopKind.READING).select_related("tour__employee")
    install_stops = TourStop.objects.filter(kind=StopKind.INSTALLATION).select_related("tour__employee")
    buildings = buildings.prefetch_related(Prefetch("tour_stops", queryset=reading_stops, to_attr="planned"))
    orders = orders.prefetch_related(Prefetch("tour_stops", queryset=install_stops, to_attr="planned"))
    storno_buildings, storno_orders = _storno()  # found anyway (a person decides), but clearly marked
    results = [Suggestion(StopKind.READING, b.pk, f"📖 Ablesung {b.file_number} · {b.street}, {b.zip_code} {b.city}",
                          f"{'⛔ Storno gemeldet · ' if b.pk in storno_buildings else ''}{b.reading_minutes} min{planned(b.planned)}",
                          b.reading_minutes)
               for b in buildings.order_by("file_number")[:limit] if (StopKind.READING, b.pk, None) not in in_plan]
    results += [Suggestion(StopKind.INSTALLATION, o.pk, f"🔧 Montage {o.re_number} · {o.street}, {o.zip_code} {o.city}",
                           f"{'⛔ Storno gemeldet · ' if o.pk in storno_orders else ''}"
                           f"{o.duration_minutes} min{' · ' + o.summary if o.summary else ''}{planned(o.planned)}",
                           o.duration_minutes)
                for o in orders.order_by("re_number")[:limit]
                if (StopKind.INSTALLATION, o.building_id, o.pk) not in in_plan]
    return results


# =============================================================================
# Team: more people on one plan (big objects)
# =============================================================================

MAX_TEAM = 3  # up to 3 more people besides the lead


def tours_with(employee):
    """Q for all tours this person works on: as lead or in the team."""
    return Q(employee=employee) | Q(team=employee)


def set_team(draft, add=None, remove=None, split=None):
    """Change the team of the draft; returns a short message."""
    team = [pk for pk in draft.get("team", []) if pk != remove]
    message = ""
    if add:
        person = Employee.objects.filter(pk=add, active=True).first()
        if person is None or person.pk == draft["employee"] or person.pk in team:
            raise ValueError("Diese Person ist schon im Plan.")
        if len(team) >= MAX_TEAM:
            raise ValueError(f"Höchstens {MAX_TEAM + 1} Personen in einem Team.")
        team.append(person.pk)
        message = f"{person} ist jetzt im Team"
    elif remove:
        message = "aus dem Team genommen"
    if split is not None:
        draft["split"] = split
        message = "Arbeitszeit wird auf das Team aufgeteilt" if split else "Jeder Stopp behält die volle Arbeitszeit"
    draft["team"] = team
    return message


def team_problems(draft, lead, date, team):
    """Why the team cannot work this plan: absent, or already in another plan that day."""
    problems = []
    others = Tour.objects.filter(date=date)
    if draft.get("tour_id"):
        others = others.exclude(pk=draft["tour_id"])
    for person in [lead, *team]:
        if person != lead and Absence.objects.filter(employee=person, start_date__lte=date, end_date__gte=date).exists():
            problems.append(f"{person} ist an diesem Tag abwesend.")
        clash = others.filter(tours_with(person)).exclude(employee=lead).select_related("employee").first()
        if clash and clash.employee == person:
            problems.append(f"{person} hat an diesem Tag schon einen eigenen Fahrplan.")
        elif clash:
            problems.append(f"{person} ist an diesem Tag schon im Plan von {clash.employee}.")
    return problems


FREE_DAY_SUGGESTIONS = 12


def _usual_region(employee):
    """The region this person mostly works in (from the buildings of their plans)."""
    regions = {}
    for stop in TourStop.objects.filter(Q(tour__employee=employee) | Q(tour__team=employee)).select_related(
            "building", "installation_order__building"):
        building = stop.building or (stop.installation_order.building if stop.installation_order else None)
        if building and building.region:
            regions[building.region] = regions.get(building.region, 0) + 1
    return max(regions, key=regions.get) if regions else ""


def _nearest_first(items):
    """Keep stops close together: start with the first one, then always the nearest next one."""
    placed = [(item, geocoding.position(item, None)[0]) for item in items]
    if not placed:
        return []
    route, rest = [placed[0]], placed[1:]
    while rest:
        last = route[-1][1]
        rest.sort(key=lambda entry: distance_km(last, entry[1]) if last and entry[1] else 9999)
        route.append(rest.pop(0))
    return [item for item, _ in route]


def free_day_suggestions(employee, date, kind=""):
    """What the system proposes for a free day of this person (AUTOMATIC: keeps to 7,5 h).

    Readers: assigned, still unplanned buildings, then others in their usual region.
    Installers: assigned orders without date, then open orders in their usual region.
    kind: "reading" / "installation" = only that (the calendar filter), "" = what the person can do.
    Returns (suggestions, ids pre-ticked because they fit into the day).
    """
    region = _usual_region(employee)
    storno_buildings, storno_orders = _storno()
    found = []
    if employee.can_install and kind != "reading":
        _, revisit_orders = visit_revisit_ids()
        open_orders = (InstallationOrder.objects.exclude(status=OrderStatus.DONE)
                       .filter(Q(tour_stops__isnull=True) | Q(pk__in=revisit_orders))
                       .exclude(pk__in=storno_orders).distinct())
        assigned = list(open_orders.filter(assigned_installers=employee).order_by("re_number"))
        rest = open_orders.exclude(pk__in=[o.pk for o in assigned]).order_by("zip_code", "re_number")
        # first the usual region, then the others (many orders have no building, so no region)
        nearby = list(rest.filter(building__region=region)[:30]) if region else []
        nearby += list(rest.exclude(pk__in=[o.pk for o in nearby])[:30 - len(nearby)])
        for order in assigned + _nearest_first(nearby):
            why = (f"{employee} zugewiesen" if order in assigned
                   else f"Region {region}" if region and order.building and order.building.region == region else "noch ohne Termin")
            found.append(Suggestion(StopKind.INSTALLATION, order.pk, f"🔧 Montage {order.re_number} · {order.street}, {order.city}",
                                    f"{why} · {order.duration_minutes} min", order.duration_minutes))
    if employee.can_read and kind != "installation":
        revisit_buildings, _ = visit_revisit_ids()  # 🔁 Nachtermin: plannable again
        unplanned = (Building.objects.filter(Q(tour_stops__isnull=True) | Q(pk__in=revisit_buildings))
                     .exclude(pk__in=storno_buildings).distinct())
        assigned = list(unplanned.filter(assigned_reader=employee).order_by("zip_code", "file_number"))
        rest = unplanned.exclude(pk__in=[b.pk for b in assigned]).order_by("zip_code", "file_number")
        nearby = list(rest.filter(region=region)[:30]) if region else []
        nearby += list(rest.exclude(pk__in=[b.pk for b in nearby])[:30 - len(nearby)])
        for building in assigned + _nearest_first(nearby):
            why = (f"{employee} zugeordnet" if building in assigned
                   else f"Region {region}" if region and building.region == region else "noch ungeplant")
            found.append(Suggestion(StopKind.READING, building.pk, f"📖 Ablesung {building.file_number} · {building.street}, {building.city}",
                                    f"{why} · {building.reading_minutes} min", building.reading_minutes))
    found = found[:FREE_DAY_SUGGESTIONS]
    ticked, used = set(), 0
    for suggestion in found:
        extra = suggestion.minutes + SUGGESTION_DRIVE_MINUTES
        if fits_in_day(used, extra):
            ticked.add((suggestion.kind, suggestion.pk))
            used += extra
    return found, ticked


# =============================================================================
# Question before saving a day that is too long / too short: options
# =============================================================================

@dataclass
class Candidate:
    """An unplanned stop near the plan (reading or installation)."""

    kind: str
    pk: int
    title: str
    minutes: int      # work
    drive: int        # estimated drive from the nearest stop of the plan
    km: float | None

    @property
    def need(self):
        return self.minutes + self.drive


def drive_to_plan(obj, points):
    """(estimated drive minutes, km) from the nearest stop of the plan to this building / order."""
    point = geocoding.position(obj, None)[0]
    nearest = min(points, key=lambda p: distance_km(p, point)) if point and points else None
    if nearest is None:
        return SUGGESTION_DRIVE_MINUTES, None
    return estimate_drive_minutes(nearest, point), distance_km(nearest, point)


def rate_search_results(results, draft, preview):
    """For the search in "Bist du sicher?": does each result fit into the day, and instead of which stop?

    Adds to every result: drive, km, fits (as an extra stop) and swaps [(index, street)].
    """
    options = time_options(draft, preview)
    net = preview.day_plan.net_minutes
    points = [s.point for s in preview.stops if s.point]
    buildings = Building.objects.in_bulk([r.pk for r in results if r.kind == StopKind.READING])
    orders = InstallationOrder.objects.in_bulk([r.pk for r in results if r.kind == StopKind.INSTALLATION])
    for result in results:
        obj = buildings.get(result.pk) if result.kind == StopKind.READING else orders.get(result.pk)
        result.drive, km = drive_to_plan(obj, points)
        result.km = round(km, 1) if km is not None else None
        need = result.minutes + result.drive
        result.fits = fits_in_day(net, need)
        result.swaps = [(t.index, t.stop.target.street) for t in options["trim"]
                        if need <= MAX_NET_MINUTES - (net - t.saves)]
    return results


def nearby_unplanned(draft, preview, room, limit=6):
    """Everything not planned yet (readings AND installations the person can do) that
    fits into `room` minutes, nearest to the plan first."""
    in_plan = {_stop_key(s) for s in draft["stops"]}
    points = [s.point for s in preview.stops if s.point]
    employee = preview.employee
    storno_buildings, storno_orders = _storno()
    revisit_buildings, revisit_orders = visit_revisit_ids()  # 🔁 Nachtermin: plannable again
    objects = []
    if employee.can_read:
        unplanned = Building.objects.exclude(tour_stops__kind=StopKind.READING) | Building.objects.filter(pk__in=revisit_buildings)
        objects += [(StopKind.READING, b) for b in unplanned.exclude(pk__in=storno_buildings).distinct()
                    if (StopKind.READING, b.pk, None) not in in_plan]
    if employee.can_install:
        unplanned = (InstallationOrder.objects.exclude(tour_stops__kind=StopKind.INSTALLATION)
                     | InstallationOrder.objects.filter(pk__in=revisit_orders))
        objects += [(StopKind.INSTALLATION, o) for o in unplanned.exclude(status=OrderStatus.DONE).distinct()
                    .exclude(pk__in=storno_orders)
                    if (StopKind.INSTALLATION, o.building_id, o.pk) not in in_plan]
    found = []
    for kind, obj in objects:
        drive, km = drive_to_plan(obj, points)
        minutes = obj.reading_minutes if kind == StopKind.READING else obj.duration_minutes
        if minutes and minutes + drive <= room:
            label = (f"📖 Ablesung {obj.file_number} · {obj.street}, {obj.city}" if kind == StopKind.READING
                     else f"🔧 Montage {obj.re_number} · {obj.street}, {obj.city}")
            found.append(Candidate(kind, obj.pk, label, minutes, drive, round(km, 1) if km is not None else None))
    found.sort(key=lambda c: (c.km is None, c.km or 0, -c.minutes))
    return found[:limit]


@dataclass
class TrimOption:
    index: int              # stop in the draft
    stop: object            # PreviewStop
    saves: int              # minutes saved when it is taken out
    swaps: list             # Candidates that fit instead


def time_options(draft, preview):
    """Options for the question dialog: what to take out / swap (too long) or add (too short)."""
    plan = preview.day_plan
    notice = preview.time_notice
    if notice is None:
        return {"kind": "", "trim": [], "enough": True, "fill": []}
    if notice.kind == "over":
        savings = [s.work_minutes + (s.drive_minutes or 0) for s in preview.stops]
        indices, enough = trim_choices(savings, plan.net_minutes)
        trim = []
        for i in indices:
            room = MAX_NET_MINUTES - (plan.net_minutes - savings[i])
            trim.append(TrimOption(i, preview.stops[i], savings[i], nearby_unplanned(draft, preview, room, limit=3) if room > 0 else []))
        return {"kind": "over", "trim": trim, "enough": enough, "fill": []}
    room = MAX_NET_MINUTES - plan.net_minutes
    return {"kind": "under", "trim": [], "enough": True, "fill": nearby_unplanned(draft, preview, room)}


def swap_stop(draft, index, kind, pk):
    """⇄: replace the stop at `index` with another one (same position)."""
    if not 0 <= index < len(draft["stops"]):
        raise ValueError("Stopp nicht gefunden.")
    old = draft["stops"].pop(index)
    try:
        message = add_stop(draft, kind, pk)
    except ValueError:
        draft["stops"].insert(index, old)
        raise
    draft["stops"].insert(index, draft["stops"].pop())  # the new stop takes the old place
    return message.replace("hinzugefügt", "statt des alten Stopps eingeplant")


def first_free_days(employees, start):
    """{employee: first free working day} - plans as lead or in a team count as busy."""
    end = start + datetime.timedelta(days=HORIZON_DAYS)
    busy, away = {}, {}
    for tour in Tour.objects.filter(date__gte=start, date__lte=end).prefetch_related("team"):
        for person in [tour.employee_id, *[e.pk for e in tour.team.all()]]:
            busy.setdefault(person, set()).add(tour.date)
    for absence in Absence.objects.filter(end_date__gte=start, start_date__lte=end):
        away.setdefault(absence.employee_id, []).append((absence.start_date, absence.end_date))
    return {e: first_free_day(start, busy.get(e.pk, set()), away.get(e.pk, [])) for e in employees}


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
    full_minutes: int = 0   # work time for ONE person (before splitting on the team)
    helpers: list = field(default_factory=list)   # help stops of other plans at this object
    people: int = 1                                 # how many work at this stop (team + helpers)
    help_tour: object = None                        # kind "help": the plan this person helps with
    help_stop: object = None                        # ... and its stop at this object (times)
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
    team: list = field(default_factory=list)          # Employees besides the lead
    split: bool = True                                 # work time divided by the team size
    team_problems: list = field(default_factory=list)
    time_notice: object = None     # rules.working_time.TimeNotice: over 7,5 h / under 6 h (info only)

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


def _helpers_of(tour_id):
    """{(building id, order id): [help TourStops]} of other plans helping in this plan."""
    found = {}
    if not tour_id:
        return found
    for stop in TourStop.objects.filter(kind=StopKind.HELP, help_tour_id=tour_id).select_related("tour__employee"):
        key = (None if stop.installation_order_id else stop.building_id, stop.installation_order_id)
        found.setdefault(key, []).append(stop)
    return found


def same_object(stop, building_id, order_id):
    return (stop.installation_order_id == order_id) if order_id else (stop.building_id == building_id and not stop.installation_order_id)


def _prepare_help(stop, raw, draft):
    """A help stop: its share of the work, and whether it still fits the helped plan."""
    helped = Tour.objects.filter(pk=raw.get("help_tour")).select_related("employee").prefetch_related("team").first()
    stop.help_tour = helped
    if helped is None:
        stop.findings.append(Finding("critical", "Der Plan, bei dem geholfen wird, wurde gelöscht – diesen Stopp mit ✕ entfernen."))
        return
    if helped.date.isoformat() != draft["date"]:
        stop.findings.append(Finding("critical", f"Der Plan von {helped.people_label} ist jetzt am {helped.date:%d.%m.%Y} – Hilfe passt nicht mehr."))
    target_stop = next((s for s in helped.stops.all() if s.kind != StopKind.HELP
                        and same_object(s, raw.get("building"), raw.get("order"))), None)
    stop.help_stop = target_stop
    if target_stop is None:
        stop.findings.append(Finding("critical", f"Das Objekt ist nicht mehr im Plan von {helped.people_label}."))
        return
    others = TourStop.objects.filter(kind=StopKind.HELP, help_tour=helped).exclude(tour_id=draft.get("tour_id"))
    others = [o for o in others if same_object(o, raw.get("building"), raw.get("order"))]
    stop.people = len(helped.people) + len(others) + 1
    stop.work_minutes = team_minutes(stop.full_minutes, stop.people)


def _check_help_times(stops):
    """Warning if the helper is not there while the others are (times of the helped plan)."""
    for stop in stops:
        target = stop.help_stop
        if stop.kind != StopKind.HELP or target is None or not target.start_time or not stop.start:
            continue
        if stop.start >= (target.end_time or target.start_time) or stop.end <= target.start_time:
            stop.findings.append(Finding("warning", (
                f"{stop.help_tour.people_label} ist dort {target.start_time:%H:%M}–{target.end_time:%H:%M} – "
                f"du kommst {stop.start:%H:%M}–{stop.end:%H:%M}. Beginn oder Reihenfolge anpassen.")))


def _load_targets(stops):
    building_ids = {s["building"] for s in stops if s.get("building")}
    order_ids = {s["order"] for s in stops if s.get("order")}
    buildings = Building.objects.in_bulk(building_ids)
    orders = InstallationOrder.objects.in_bulk(order_ids)
    targets = {}
    for stop in stops:
        if stop["kind"] == StopKind.READING or (stop["kind"] == StopKind.HELP and not stop.get("order")):
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
    team = list(Employee.objects.filter(pk__in=draft.get("team", [])).order_by("short_name"))
    split = draft.get("split", True)

    helpers = _helpers_of(draft.get("tour_id"))   # people from other plans helping at objects of this plan
    stops, error = [], ""
    for i, raw in enumerate(draft["stops"]):
        target = targets[_stop_key(raw)]
        on_building = isinstance(target, Building)
        building = target if on_building else target.building
        full = target.reading_minutes if on_building else target.duration_minutes
        point, source, warnings = geocoding.position(target, client)
        if source == "tomtom" and point is None:
            source = None
        stop = PreviewStop(
            index=i, kind=raw["kind"], building=building, order=None if on_building else target, target=target,
            work_minutes=full, full_minutes=full, point=point, point_source=source, warnings=warnings,
        )
        if raw["kind"] == StopKind.HELP:
            _prepare_help(stop, raw, draft)
        else:
            stop.helpers = helpers.get((raw.get("building") if on_building else None, raw.get("order")), [])
            stop.people = (1 + len(team) if split else 1) + len(stop.helpers)
            stop.work_minutes = team_minutes(full, stop.people)
        stops.append(stop)

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
                      error=error, has_tomtom=client is not None, team=team, split=split,
                      team_problems=team_problems(draft, employee, date, team))
    preview.distance_km = sum((s.drive_km or Decimal("0")) for s in stops)
    _add_findings(preview, draft)
    _check_help_times(preview.stops)
    preview.time_notice = time_notice(plan)  # only information: the person planning decides
    preview.problems = confirmation_problems(plan, preview.all_from_tomtom and not error) + preview.team_problems
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
    visited = _visited_here(draft)
    buildings = [b for b in buildings if b.pk not in visited]
    stops = (TourStop.objects.filter(kind=StopKind.READING, building__in=buildings, done_at__isnull=True, outcome="")
             .exclude(tour__employee=preview.employee, tour__date=preview.date))  # a visited stop stays as history
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
    for stop in (TourStop.objects.filter(kind=StopKind.INSTALLATION, installation_order__building__in=[s.building for s in reading],
                                         done_at__isnull=True, outcome="")
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
    if preview.team_problems:
        raise ValueError(" ".join(preview.team_problems))  # also for "vorläufig": nobody may be booked twice
    if confirm and not preview.can_confirm:
        raise ValueError(" ".join(preview.problems))

    tour = _lock_tour(draft)
    before = (tour.employee, tour.date, tour.status) if tour is not None else None  # for the Verlauf
    if tour is None:
        tour = Tour(employee=preview.employee, date=preview.date, created_by=user)
    carried = _take_open_stops_from_split(draft, preview, user)  # a moved plan with reported stops

    carried.update(_move_buildings_out_of_other_tours(preview, draft, user))

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
    tour.split_work = preview.split
    tour.save()

    tour.team.set(preview.team)
    helped_before = set(tour.stops.filter(kind=StopKind.HELP).values_list("help_tour_id", flat=True)) if tour.pk else set()
    # printed tenant notices stay with their object (they show "veraltet" if day or time changed)
    # (and so does the choice "Aushang ja")
    # ... and what was reported from "Mein Tag" (Ergebnis, done, note on site, the Visit)
    reported = {(st.kind, st.building_id, st.installation_order_id): st
                for st in tour.stops.filter(Q(done_at__isnull=False) | ~Q(field_note="") | ~Q(outcome=""))} if tour.pk else {}
    visit_of = dict(Visit.objects.filter(stop__in=list(reported.values())).values_list("stop_id", "pk"))
    notices = dict(carried)  # notices of stops that came from other plans (they show "veraltet" if the day changed)
    if tour.pk:
        for st in tour.stops.filter(NOTICE_STORED):
            notices.update(_notice_of(st))
    tour.stops.all().delete()
    for position, stop in enumerate(preview.stops, start=1):
        notice = notices.get((stop.kind, stop.building.pk if stop.building else None, stop.order.pk if stop.order else None), {})
        done = reported.get((stop.kind, stop.building.pk if stop.building else None, stop.order.pk if stop.order else None))
        new_stop = TourStop.objects.create(
            outcome=done.outcome if done else "", done_at=done.done_at if done else None,
            done_by_id=done.done_by_id if done else None, field_note=done.field_note if done else "",
            **notice,
            tour=tour, position=position, kind=stop.kind, building=stop.building, installation_order=stop.order,
            help_tour=stop.help_tour if stop.kind == StopKind.HELP else None,
            start_time=stop.start, end_time=stop.end, work_minutes=stop.work_minutes,
            drive_to_next_seconds=stop.drive_seconds, drive_to_next_minutes=stop.drive_minutes,
            drive_to_next_km=stop.drive_km, departure_time=stop.departure,
            drive_source=stop.drive_source if position < len(preview.stops) else DriveSource.NONE,
            route_warnings=stop.warnings,
        )
        if done and done.pk in visit_of:
            Visit.objects.filter(pk=visit_of[done.pk]).update(stop=new_stop)  # the Ergebnis stays with the stop

    _record_saved(user, tour, before, preview)

    # The helped plans get less work at that object (or more, if a help was removed): recalculate them
    helped_now = {s.help_tour.pk for s in preview.stops if s.kind == StopKind.HELP and s.help_tour}
    _mark_helped(helped_before | helped_now, f"🤝 Hilfe von {preview.employee} geändert – bitte neu rechnen "
                                             "(die Arbeitszeit am Objekt wird aufgeteilt)")

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


def _mark_helped(tour_ids, reason):
    for helped in Tour.objects.filter(pk__in=[pk for pk in tour_ids if pk]):
        helped.needs_recalculation, helped.change_reason = True, reason
        helped.version += 1  # counts as a change for optimistic locking
        helped.save()


def help_draft(stop, helper):
    """🤝 "Helfer" in the calendar: the helper's day with a help stop at this object.

    The helper keeps an own plan that day (or gets a new one). Nothing is saved
    here - the draft opens in "Fahrplan prüfen".
    """
    helped = stop.tour
    if helper in helped.people:
        raise ValueError(f"{helper} arbeitet schon in diesem Plan mit.")
    if Tour.objects.filter(team=helper, date=helped.date).exists():
        raise ValueError(f"{helper} ist an diesem Tag fest in einem Team eingeplant und kann nicht woanders helfen.")
    if stop.kind == StopKind.HELP:
        raise ValueError("Bei einem Hilfe-Stopp kann nicht noch einmal geholfen werden.")
    own = Tour.objects.filter(employee=helper, date=helped.date).first()
    draft = (draft_from_tour(own) if own
             else create_draft([], helper, helped.date, helper.default_start_time, 30, "far"))
    new = {"kind": StopKind.HELP, "building": stop.building_id, "order": stop.installation_order_id, "help_tour": helped.pk}
    if _stop_key(new) in {_stop_key(s) for s in draft["stops"]}:
        raise ValueError(f"{helper} hilft dort schon.")
    draft["stops"].append(new)
    return draft


def _record_saved(user, tour, before, preview):
    """🕘 Verlauf: created / moved / confirmed / changed."""
    from journal.activity import day_label, record, streets
    from journal.models import ActivityKind

    people = " + ".join([str(preview.employee), *[str(e) for e in preview.team]])
    what = f"{len(preview.stops)} Stopps: {streets(preview.stops)}"
    state = "bestätigt" if tour.status == TourStatus.CONFIRMED else "vorläufig"
    if before is None:
        text = f"Fahrplan erstellt ({state}): {people} {day_label(tour.date)} · {what}"
    elif (before[0], before[1]) != (tour.employee, tour.date):
        text = f"Termin verschoben: {before[0]} {day_label(before[1])} → {people} {day_label(tour.date)} · {what}"
    elif tour.status == TourStatus.CONFIRMED and before[2] != TourStatus.CONFIRMED:
        text = f"Fahrplan bestätigt: {people} {day_label(tour.date)} · {what}"
    else:
        text = f"Fahrplan geändert ({state}): {people} {day_label(tour.date)} · {what}"
    record(user, ActivityKind.PLAN, text, tour=tour)


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


# The tenant notice of an appointment stays with the object when the plan is saved again, moved
# or split (a printed Aushang then shows "veraltet"): printed?, for whom, the time by hand.
NOTICE_KEEP = ["notice_printed_at", "notice_for", "notice_wanted", "notice_printed_by_id", "notice_scope",
               "notice_units", "notice_from", "notice_to"]
NOTICE_STORED = (Q(notice_wanted=True) | Q(notice_printed_at__isnull=False) | ~Q(notice_units="")
                 | Q(notice_from__isnull=False) | ~Q(notice_scope="haus"))


def _has_notice_data(stop):
    return bool(stop.notice_wanted or stop.notice_printed_at or stop.notice_units or stop.notice_from
                or stop.notice_scope != "haus")


def _notice_of(stop):
    return {(stop.kind, stop.building_id, stop.installation_order_id): {f: getattr(stop, f) for f in NOTICE_KEEP}} \
        if _has_notice_data(stop) else {}


def _take_open_stops_from_split(draft, preview, user):
    """Moving a plan with reported stops: its open stops leave the old plan (the reported ones stay there)."""
    if not draft.get("split_from"):
        return {}
    updated = Tour.objects.filter(pk=draft["split_from"], version=draft["split_version"]).update(version=F("version") + 1)
    if not updated:
        raise ConcurrentChange("Der Fahrplan wurde inzwischen von jemand anderem geändert.")
    old = Tour.objects.get(pk=draft["split_from"])
    carried = {}
    moving = [st for st in old.stops.all() if not is_visited(st)]
    for stop in moving:
        carried.update(_notice_of(stop))
        stop.delete()
    for position, stop in enumerate(old.stops.order_by("position"), start=1):
        if stop.position != position:
            stop.position = position
            stop.save(update_fields=["position"])
    from journal.activity import day_label, record, streets
    from journal.models import ActivityKind
    record(user, ActivityKind.PLAN, f"{len(moving)} offene Stopps verschoben: {old.employee} {day_label(old.date)} → "
           f"{preview.employee} {day_label(preview.date)} · {streets(moving)} (die gemeldeten bleiben als Nachweis)", tour=old)
    return carried


def _move_buildings_out_of_other_tours(preview, draft, user=None):
    """A building can only be in one tour: take it out of the others (prototype behaviour).

    Returns the notice data of the removed stops (a printed Aushang then shows "veraltet" in the new plan).
    """
    from journal.activity import day_label, record, streets
    from journal.models import ActivityKind

    buildings = [s.building for s in preview.stops if s.kind == StopKind.READING]
    old_stops = _stops_in_other_tours(preview, draft, buildings)
    changed_tours, carried = {}, {}
    for stop in old_stops:
        changed_tours.setdefault(stop.tour, []).append(stop.building)
        carried.update(_notice_of(stop))
        stop.delete()
    for tour, moved in changed_tours.items():
        remaining = list(tour.stops.order_by("position"))
        where = f"{preview.employee} {day_label(preview.date)}"
        if not remaining:
            record(user, ActivityKind.PLAN, f"Fahrplan gelöscht (leer nach Verschieben nach {where}): {tour.employee} "
                   f"{day_label(tour.date)} · {streets(moved)}")
            tour.delete()
            continue
        record(user, ActivityKind.PLAN, f"{len(moved)} Liegenschaft(en) aus {tour.employee} {day_label(tour.date)} "
               f"in den Plan {where} verschoben: {streets(moved)}", tour=tour)
        count = len(moved)
        for position, stop in enumerate(remaining, start=1):
            if stop.position != position:
                stop.position = position
                stop.save(update_fields=["position"])
        tour.needs_recalculation = True
        tour.change_reason = (f"{count} Liegenschaft(en) in den Plan {preview.employee} "
                              f"{preview.date:%d.%m.%Y} verschoben – bitte neu mit TomTom rechnen")
        tour.version += 1  # counts as a change for optimistic locking
        tour.save()
    return carried


def delete_tour(tour):
    """Delete a tour; its buildings become 'unplanned' again (kept in the history).

    Stops already reported from Mein Tag are proof of a visit: they stay, only the open stops go
    (the plan then remains with its reported stops). Returns True if the whole plan was deleted.
    """
    visited = [stop for stop in tour.stops.all() if is_visited(stop)]
    if visited:
        stops = [stop for stop in tour.stops.all() if not is_visited(stop)]
        for stop in stops:
            stop.delete()
        for position, stop in enumerate(tour.stops.order_by("position"), start=1):
            if stop.position != position:
                stop.position = position
                stop.save(update_fields=["position"])
        tour.version += 1
        tour.save()
        _after_removal(tour, stops)
        return False
    stops = list(tour.stops.all())
    buildings = [stop.building for stop in stops if stop.building and stop.kind != StopKind.HELP]
    order_ids = [stop.installation_order_id for stop in stops if stop.installation_order_id and stop.kind != StopKind.HELP]
    # plans helped by this one, and plans whose people helped here: recalculate them
    helped = {stop.help_tour_id for stop in stops if stop.kind == StopKind.HELP}
    helpers = set(tour.help_stops.values_list("tour_id", flat=True))
    tour.delete()
    _mark_helped(helped, f"🤝 Hilfe fällt weg (Plan {tour.employee} gelöscht) – bitte neu rechnen")
    _mark_helped(helpers, f"🤝 Der Plan {tour.employee} {tour.date:%d.%m.%Y} wurde gelöscht – Hilfe-Stopp bitte entfernen")
    _after_removal(None, stops, buildings, order_ids)
    return True


def _after_removal(tour, stops, buildings=None, order_ids=None):
    """Removed stops: deadlines, orders without appointment open again, conflicts."""
    if buildings is None:
        buildings = [stop.building for stop in stops if stop.building and stop.kind != StopKind.HELP]
        order_ids = [stop.installation_order_id for stop in stops if stop.installation_order_id and stop.kind != StopKind.HELP]
    for building in buildings:
        refresh_deadline(building)
    # orders without any appointment left are open again (not when an installation was already reported there)
    for order in (InstallationOrder.objects.filter(pk__in=order_ids, status=OrderStatus.PLANNED, tour_stops__isnull=True)
                  .exclude(visits__outcome="complete")):
        order.status = OrderStatus.OPEN
        order.save()
    refresh_conflicts_for([b.pk for b in buildings], order_ids)


# =============================================================================
# 🤖 Automatic planning: proposals for a period (nothing saved until confirmed)
# =============================================================================

AUTOPLAN_KEY = "autoplan"


def working_days(start, end):
    day, days = start, []
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += datetime.timedelta(days=1)
    return days


def autoplan_slots(employees, start, end):
    """Free working days (no plan as lead / in a team, not absent) of these people."""
    days = working_days(start, end)
    busy = {}
    for tour in Tour.objects.filter(date__gte=start, date__lte=end).prefetch_related("team"):
        for person in [tour.employee_id, *[e.pk for e in tour.team.all()]]:
            busy.setdefault(person, set()).add(tour.date)
    for absence in Absence.objects.filter(end_date__gte=start, start_date__lte=end):
        for day in days:
            if absence.start_date <= day <= absence.end_date:
                busy.setdefault(absence.employee_id, set()).add(day)
    return [autoplan_rules.Slot(e.pk, day, e.can_read, e.can_install, _usual_region(e))
            for e in employees for day in days if day not in busy.get(e.pk, set())]


def autoplan_jobs(kind):
    """Everything not planned yet, as jobs for the rule (with date windows from section 8)."""
    buffer = datetime.timedelta(days=autoplan_rules.BUFFER_DAYS)
    revisit_buildings, revisit_orders = visit_revisit_ids()  # 🔁 Nachtermine are planned automatically, too
    jobs = []
    if kind in ("", "reading"):
        installs = {}  # building number core -> latest planned installation (still to do)
        for stop in (TourStop.objects.filter(OPEN_STOP, kind=StopKind.INSTALLATION)
                     .select_related("tour", "installation_order__building")):
            order = stop.installation_order
            core = order.building.file_number_core if order.building else order.building_file_number_core
            if core:
                installs[core] = max(installs.get(core, stop.tour.date), stop.tour.date)
        # not planned, and not "freigegeben" (released = already done)
        unplanned = Building.objects.exclude(tour_stops__kind=StopKind.READING) | Building.objects.filter(pk__in=revisit_buildings)
        for b in unplanned.exclude(status=BuildingStatus.RELEASED).exclude(pk__in=_storno()[0]).distinct():
            jobs.append(autoplan_rules.Job(
                key=(StopKind.READING, b.pk), kind="reading", minutes=b.reading_minutes,
                point=geocoding.position(b, None)[0], region=b.region, person=b.assigned_reader_id,
                earliest=installs[b.file_number_core] + buffer if b.file_number_core in installs else None,
                group=b.file_number_core))
    if kind in ("", "installation"):
        readings = {}  # building number core -> current reading appointment (the next open one, else the last visit)
        current = {}
        for stop in current_first(TourStop.objects.filter(kind=StopKind.READING)).select_related("tour", "building"):
            current.setdefault(stop.building_id, stop)
        for stop in current.values():
            core = stop.building.file_number_core
            readings[core] = min(readings.get(core, stop.tour.date), stop.tour.date)
        unplanned = (InstallationOrder.objects.exclude(tour_stops__kind=StopKind.INSTALLATION)
                     | InstallationOrder.objects.filter(pk__in=revisit_orders))
        for o in (unplanned.exclude(status=OrderStatus.DONE).exclude(pk__in=_storno()[1]).distinct()
                  .select_related("building").prefetch_related("assigned_installers")):
            installers = sorted(o.assigned_installers.all(), key=lambda e: e.short_name)
            core = o.building.file_number_core if o.building else o.building_file_number_core
            jobs.append(autoplan_rules.Job(
                key=(StopKind.INSTALLATION, o.pk), kind="installation", minutes=o.duration_minutes,
                point=geocoding.position(o, None)[0], region=o.building.region if o.building else "",
                person=installers[0].pk if installers else None,
                latest=readings[core] - buffer if core in readings else None, group=core))
    return [job for job in jobs if job.minutes]


def autoplan(employees, start, end, kind=""):
    """Compute proposals; returns a dict for the session (plain values only)."""
    people = [e for e in employees if (kind != "reading" or e.can_read) and (kind != "installation" or e.can_install)]
    proposals, left = autoplan_rules.plan_days(autoplan_slots(people, start, end), autoplan_jobs(kind))
    days = []
    for p in proposals:
        stops = []
        for job in p.jobs:
            stop_kind, pk = job.key
            stops.append({"kind": stop_kind, "building": pk} if stop_kind == StopKind.READING else {"kind": stop_kind, "order": pk})
        days.append({"employee": p.person, "date": p.date.isoformat(), "stops": stops, "work": p.work, "drive": p.drive})
    return {"start": start.isoformat(), "end": end.isoformat(), "kind": kind, "days": days, "left": len(left)}


def autoplan_recount(day):
    """Work and estimated drive of a proposed day again (after a stop was removed)."""
    buildings = Building.objects.in_bulk([s["building"] for s in day["stops"] if s["kind"] == StopKind.READING])
    orders = InstallationOrder.objects.in_bulk([s["order"] for s in day["stops"] if s["kind"] == StopKind.INSTALLATION])
    targets = [buildings.get(s.get("building")) if s["kind"] == StopKind.READING else orders.get(s.get("order")) for s in day["stops"]]
    targets = [t for t in targets if t is not None]
    points = [geocoding.position(t, None)[0] for t in targets]
    day["work"] = sum(t.reading_minutes if isinstance(t, Building) else t.duration_minutes for t in targets)
    day["drive"] = sum(autoplan_rules.estimated_drive(a, b) for a, b in zip(points, points[1:]))
    return day


def autoplan_draft(day):
    """One proposed day as a normal draft (then "Fahrplan prüfen" with exact TomTom times)."""
    employee = Employee.objects.get(pk=day["employee"])
    return create_draft([s["building"] for s in day["stops"] if s["kind"] == StopKind.READING], employee,
                        datetime.date.fromisoformat(day["date"]), employee.default_start_time, 30, "far",
                        order_ids=[s["order"] for s in day["stops"] if s["kind"] == StopKind.INSTALLATION])


def autoplan_save_all(proposal, user):
    """Save every proposed day as a PROVISIONAL plan. Returns (saved tours, problems)."""
    saved, problems = [], []
    for day in proposal["days"]:
        try:
            with transaction.atomic():
                saved.append(save_draft(autoplan_draft(day), user, confirm=False))
        except (ValueError, ConcurrentChange) as error:
            problems.append(f"{day['date']}: {error}")
    return saved, problems
