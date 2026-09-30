"""
🛰 Wer ist wo? - database side of rules/whereabouts.py: every plan of a day and where its
people should be at the chosen time according to the Fahrplan (not GPS).
"""

from dataclasses import dataclass, field

from .calendar import tour_kind
from .models import Tour
from .rules import whereabouts as rules


@dataclass
class Position:
    tour: object
    people: str
    colour: str
    kind: str
    stops: list                     # rules.PlanStop
    where: object                   # rules.Where
    text: str = ""
    behind: list = field(default_factory=list)
    reported: int = 0
    last_report: object = None      # datetime of the newest Ergebnis
    estimated: bool = False         # the plan has no saved times: worked out from start + minutes

    @property
    def label(self):
        return rules.STATE_LABELS[self.where.state]

    @property
    def current(self):
        """The stop the plan puts the person at (or drives to)."""
        index = self.where.next_stop if self.where.next_stop is not None else self.where.stop
        return self.stops[index] if index is not None else None


def _point(target):
    from .geocoding import zip_centre  # local import: geocoding imports the TomTom client

    if target is None:
        return None
    if target.latitude is not None and target.longitude is not None:
        return (float(target.latitude), float(target.longitude))
    return zip_centre(target.zip_code)


def _label(target):
    return f"{target.street}, {target.city}" if target else ""


def _plan(tour, stops):
    """[PlanStop] from the saved times; a plan without them (e.g. imported) is worked out from its
    start + work minutes + drive minutes (ESTIMATED_DRIVE when unknown), like the planner does."""
    from .rules.working_time import DEFAULT_BREAK_MINUTES, schedule_day

    points = [_point(s.building or s.installation_order) for s in stops]
    if all(s.start_time and s.end_time for s in stops):
        return [rules.PlanStop(n=i + 1, start=rules.minutes(s.start_time), end=rules.minutes(s.end_time),
                               departure=rules.minutes(s.departure_time), drive=s.drive_to_next_minutes,
                               point=points[i], label=_label(s.building or s.installation_order),
                               reported=bool(s.done_at or s.outcome)) for i, s in enumerate(stops)], False
    drives = [s.drive_to_next_minutes if s.drive_to_next_minutes is not None else rules.ESTIMATED_DRIVE for s in stops[:-1]]
    day = schedule_day(tour.start_time, [s.work_minutes or 0 for s in stops], drives,
                       tour.break_minutes if tour.break_minutes else DEFAULT_BREAK_MINUTES)
    return [rules.PlanStop(n=i + 1, start=rules.minutes(times.start), end=rules.minutes(times.end),
                           departure=rules.minutes(times.departure), drive=drives[i] if i < len(drives) else None,
                           point=points[i], label=_label(s.building or s.installation_order),
                           reported=bool(s.done_at or s.outcome))
            for i, (s, times) in enumerate(zip(stops, day.stops))], True


def positions(day, t, kind="", today=None):
    """[Position] of all plans on `day` at minute `t`, sorted by state (vor Ort first) and people."""
    tours = (Tour.objects.filter(date=day).select_related("employee").prefetch_related(
        "team", "stops__building", "stops__installation_order"))
    found = []
    for tour in tours:
        stops = sorted(tour.stops.all(), key=lambda s: s.position)
        if not stops:
            continue
        this_kind = tour_kind(stops)
        if kind and this_kind not in (kind, "mixed"):
            continue
        plan, estimated = _plan(tour, stops)
        where = rules.where_at(plan, t, tour.commute_to_minutes, tour.commute_from_minutes)
        reports = [s.done_at for s in stops if s.done_at]
        found.append(Position(
            tour=tour, people=tour.people_label, colour=tour.employee.calendar_color or "#0d6efd", kind=this_kind,
            stops=plan, where=where, text=rules.describe(where, plan),
            behind=rules.behind_plan(plan, where, t) if today and day == today else [],
            reported=sum(1 for s in plan if s.reported), last_report=max(reports) if reports else None,
            estimated=estimated))
    order = {state: i for i, state in enumerate(rules.STATE_ORDER)}
    found.sort(key=lambda p: (order[p.where.state], p.people.lower()))
    return found


def map_data(found):
    """JSON for static/js/where_map.js: one moving marker per plan, its stops and the planned line."""
    people = []
    for p in found:
        if p.where.point is None:
            continue
        points = [s for s in p.stops if s.point]
        people.append({
            "id": p.tour.pk, "label": p.people, "short": p.tour.employee.short_name[:3], "colour": p.colour,
            "state": p.where.state, "stateLabel": p.label, "text": p.text,
            "lat": p.where.point[0], "lon": p.where.point[1], "behind": p.behind,
            "stops": [{"n": s.n, "lat": s.point[0], "lon": s.point[1], "label": s.label, "done": s.reported,
                       "time": f"{rules.clock(s.start)}–{rules.clock(s.end)}"} for s in points],
            "line": [[s.point[1], s.point[0]] for s in points],
        })
    return {"people": people}
