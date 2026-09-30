"""
🗓 Wochenplanung - database side of rules/week.py: the board (people × Monday-Friday),
the list "Noch nicht geplant" and saving the week's days as provisional plans.

BY HAND ONLY: the office decides which object goes to whom on which day. Nothing is spread
automatically. The draft of a week lives in the session (like the other planning drafts)
until "Alle vorläufig erstellen".
"""

import datetime
from dataclasses import dataclass, field

from buildings.models import Building, InstallationOrder

from . import services
from .models import Absence, Employee, StopKind, Tour
from .rules import week as rules

WEEK_KEY = "week_plan"
POOL_LIMIT = 60


# --- the draft of a week (session) ------------------------------------------------------------

def get_days(session, monday):
    return list(session.get(WEEK_KEY, {}).get(monday.isoformat(), []))


def set_days(session, monday, days):
    weeks = dict(session.get(WEEK_KEY, {}))
    if days:
        weeks[monday.isoformat()] = days
    else:
        weeks.pop(monday.isoformat(), None)
    session[WEEK_KEY] = weeks


def recount(days, cells):
    """Work + estimated drive again for the changed (employee, date) cells."""
    for day in days:
        if (day["employee"], day["date"]) in cells:
            services.autoplan_recount(day)
    return days


# --- what the board shows -----------------------------------------------------------------------

@dataclass
class Cell:
    employee: object
    date: datetime.date
    state: str
    tour: object = None           # an existing plan (lead or team)
    absence: object = None
    items: list = field(default_factory=list)   # [{value, ref, street, city, minutes, hint}]
    minutes: int = 0
    level: str = ""

    @property
    def iso(self):
        return self.date.isoformat()

    @property
    def droppable(self):
        return self.state in (rules.FREE, rules.DRAFT)

    @property
    def hours(self):
        return rules.hours(self.minutes)


def _targets(days):
    buildings = Building.objects.in_bulk([s["building"] for d in days for s in d["stops"] if s["kind"] == StopKind.READING])
    orders = InstallationOrder.objects.select_related("building").in_bulk(
        [s["order"] for d in days for s in d["stops"] if s["kind"] == StopKind.INSTALLATION])
    return buildings, orders


def _describe(item, buildings, orders, windows, date):
    target = buildings.get(item.get("building")) if item["kind"] == StopKind.READING else orders.get(item.get("order"))
    if target is None:
        return None
    earliest, latest = windows.get(rules.item_key(item), (None, None))
    return {
        "value": rules.item_value(item), "kind": item["kind"],
        "ref": f"AZ {target.file_number}" if item["kind"] == StopKind.READING else f"RE {target.re_number}",
        "street": target.street, "city": target.city,
        "minutes": target.reading_minutes if item["kind"] == StopKind.READING else target.duration_minutes,
        "hint": rules.window_hint(date, earliest, latest),
    }


def people_for(kind):
    people = Employee.objects.filter(active=True)
    if kind == "reading":
        people = people.filter(can_read=True)
    elif kind == "installation":
        people = people.filter(can_install=True)
    return list(people.order_by("short_name"))


def board(monday, kind, days, windows):
    """[(employee, [Cell × 5])] for the week."""
    dates = rules.week_days(monday)
    people = people_for(kind)
    planned, team = {}, {}
    for tour in (Tour.objects.filter(date__gte=dates[0], date__lte=dates[-1])
                 .select_related("employee").prefetch_related("team", "stops")):
        planned[(tour.employee_id, tour.date)] = tour
        for member in tour.team.all():
            team[(member.pk, tour.date)] = tour
    away = {}
    for absence in Absence.objects.filter(end_date__gte=dates[0], start_date__lte=dates[-1]):
        for date in dates:
            if absence.start_date <= date <= absence.end_date:
                away[(absence.employee_id, date)] = absence
    buildings, orders = _targets(days)
    rows = []
    for person in people:
        cells = []
        for date in dates:
            day = rules.find_day(days, person.pk, date.isoformat())
            state = rules.cell_state((person.pk, date) in away, (person.pk, date) in planned, (person.pk, date) in team,
                                     bool(day))
            cell = Cell(person, date, state, tour=planned.get((person.pk, date)) or team.get((person.pk, date)),
                        absence=away.get((person.pk, date)))
            if day:
                cell.items = [i for i in (_describe(s, buildings, orders, windows, date) for s in day["stops"]) if i]
                cell.minutes, cell.level = rules.day_load(day.get("work"), day.get("drive"), person.max_daily_minutes)
            cells.append(cell)
        rows.append((person, cells))
    return rows


def pool(kind, query, days, ticked_buildings, ticked_orders):
    """ "Noch nicht geplant": open objects without an appointment (and 🔁 Nachtermine) that are not
    in this week's draft. Returns (shown items, how many in all, windows {key: (earliest, latest)})."""
    from .visits import revisit_ids

    jobs = services.autoplan_jobs(kind)   # the same "what is still open" list (it does NOT plan anything)
    windows = {job.key: (job.earliest, job.latest) for job in jobs}
    in_week = {rules.item_key(s) for d in days for s in d["stops"]}
    jobs = [job for job in jobs if job.key not in in_week]
    buildings = Building.objects.select_related("assigned_reader").in_bulk(
        [pk for kind_, pk in (j.key for j in jobs) if kind_ == StopKind.READING])
    orders = InstallationOrder.objects.select_related("building").prefetch_related("assigned_installers").in_bulk(
        [pk for kind_, pk in (j.key for j in jobs) if kind_ == StopKind.INSTALLATION])
    revisit_b, revisit_o = revisit_ids()
    words = query.lower().split()
    items = []
    for job in jobs:
        stop_kind, pk = job.key
        target = buildings.get(pk) if stop_kind == StopKind.READING else orders.get(pk)
        if target is None:
            continue
        if stop_kind == StopKind.READING:
            ref, person = f"AZ {target.file_number}", target.assigned_reader.short_name if target.assigned_reader else ""
            ticked, revisit = pk in ticked_buildings, pk in revisit_b
        else:
            installers = sorted(target.assigned_installers.all(), key=lambda e: e.short_name)
            ref, person = f"RE {target.re_number}", installers[0].short_name if installers else ""
            ticked, revisit = pk in ticked_orders, pk in revisit_o
        item = {"value": f"{stop_kind}:{pk}", "kind": stop_kind, "ref": ref, "street": target.street,
                "zip": target.zip_code or "", "city": target.city, "minutes": job.minutes, "person": person,
                "ticked": ticked, "revisit": revisit, "earliest": job.earliest, "latest": job.latest}
        if words and not all(w in " ".join([ref, target.street, item["zip"], target.city, person]).lower() for w in words):
            continue
        items.append(item)
    items.sort(key=rules.pool_sort_key)
    return items[:POOL_LIMIT], len(items), windows


def cell_problem(employee, date, monday):
    """Why nothing may be put on this cell ('' = ok): outside the week, absent, already planned."""
    if date not in rules.week_days(monday):
        return "Dieser Tag gehört nicht zur gewählten Woche."
    if Absence.objects.filter(employee=employee, start_date__lte=date, end_date__gte=date).exists():
        return f"{employee} ist an diesem Tag abwesend."
    if Tour.objects.filter(employee=employee, date=date).exists() or Tour.objects.filter(team=employee, date=date).exists():
        return f"{employee} hat an diesem Tag schon einen Fahrplan – bitte dort im Kalender ergänzen."
    return ""


def save_week(days, monday, user):
    """Every day of the week's draft as a PROVISIONAL plan (like any other plan - confirm them in the
    calendar). Days whose cell is no longer free are kept back. Returns (saved tours, problems, kept days)."""
    saved, problems, kept = [], [], []
    people = Employee.objects.in_bulk([d["employee"] for d in days])
    for day in days:
        employee, date = people.get(day["employee"]), datetime.date.fromisoformat(day["date"])
        problem = "Person nicht gefunden." if employee is None else cell_problem(employee, date, monday)
        if problem:
            problems.append(f"{date:%d.%m.}: {problem}")
            kept.append(day)
            continue
        done, failed = services.autoplan_save_all({"days": [day]}, user)
        saved += done
        if failed:
            problems += failed
            kept.append(day)
    return saved, problems, kept
