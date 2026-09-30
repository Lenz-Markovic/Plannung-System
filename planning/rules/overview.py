"""
📊 Übersicht - the numbers behind the dashboard (pure functions, no database).

Time range, visits per day (or per week for long ranges), how many Termine were needed,
the reasons for "nicht erledigt", the work per person and clean axis steps for the charts.
"""

import datetime
import math
from dataclasses import dataclass

COMPLETE, PARTIAL, ABSENT = "complete", "partial", "absent"
OUTCOME_ORDER = [COMPLETE, PARTIAL, ABSENT]
PERIODS = [("7", "letzte 7 Tage"), ("30", "letzte 30 Tage"), ("90", "letzte 90 Tage"), ("saison", "ganze Saison")]
PERIOD_KEYS = [key for key, _ in PERIODS]
WEEKS_FROM_DAYS = 45      # a longer range is shown per week, not per day


def chosen_period(value):
    return value if value in PERIOD_KEYS else "30"


def period_range(key, today, season_start=None):
    """(first day, last day) - always up to today. 'saison' = from the first visit/plan on."""
    if key == "saison":
        start = season_start or today - datetime.timedelta(days=29)
        return min(start, today), today
    return today - datetime.timedelta(days=int(key) - 1), today


def week_start(day):
    return day - datetime.timedelta(days=day.weekday())


def buckets(start, end):
    """The columns of the chart: every day, or every week (Monday) for a long range.
    Saturdays and Sundays only appear as days when something happened (see visits_per_bucket)."""
    per_week = (end - start).days + 1 > WEEKS_FROM_DAYS
    if per_week:
        first, found = week_start(start), []
        while first <= end:
            found.append(first)
            first += datetime.timedelta(days=7)
        return found, True
    return [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)], False


@dataclass
class Bucket:
    day: datetime.date
    per_week: bool
    complete: int = 0
    partial: int = 0
    absent: int = 0

    @property
    def total(self):
        return self.complete + self.partial + self.absent

    @property
    def label(self):
        return f"KW {self.day.isocalendar()[1]}" if self.per_week else f"{self.day:%d.%m.}"


def visits_per_bucket(start, end, visits):
    """visits: (date, outcome) pairs -> [Bucket]. Weekend days without visits are left out."""
    days, per_week = buckets(start, end)
    found = {d: Bucket(d, per_week) for d in days}
    for date, outcome in visits:
        if not start <= date <= end or outcome not in OUTCOME_ORDER:
            continue
        key = week_start(date) if per_week else date
        bucket = found.get(key)
        if bucket is not None:
            setattr(bucket, outcome, getattr(bucket, outcome) + 1)
    return [b for d, b in sorted(found.items()) if per_week or d.weekday() < 5 or b.total]


def nice_max(value, ticks=4):
    """A clean top of the axis and its steps: 37 -> (40, [0, 10, 20, 30, 40])."""
    if value <= 0:
        return 1, [0, 1]
    raw = value / ticks
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= raw)
    step = max(1, int(step)) if step >= 1 else step
    top = step * math.ceil(value / step)
    count = int(round(top / step))
    return top, [step * i for i in range(count + 1)]


def percent(part, whole):
    return round(100 * part / whole) if whole else 0


def attempts_needed(attempts):
    """Visits that ended ✓ fertig, by their Termin number: {'1.': n, '2.': n, '3. +': n}."""
    found = {"1.": 0, "2.": 0, "3. +": 0}
    for n in attempts:
        found["1." if n <= 1 else "2." if n == 2 else "3. +"] += 1
    return found


def count_reasons(reasons, labels):
    """✗ nicht erledigt by reason, the most frequent first: [(label, n)]."""
    counts = {}
    for reason in reasons:
        counts[reason] = counts.get(reason, 0) + 1
    order = list(labels)
    return sorted(((labels.get(r, "ohne Grund"), n) for r, n in counts.items()),
                  key=lambda item: (-item[1], order.index(item[0]) if item[0] in order else 99))


@dataclass
class PersonRow:
    people: str
    planned: int = 0        # stops in the plans of the range
    reported: int = 0       # of those with an Ergebnis
    complete: int = 0
    partial: int = 0
    absent: int = 0

    @property
    def open(self):
        return max(0, self.planned - self.reported)

    @property
    def success(self):
        """✓ fertig out of all reported (in %)."""
        return percent(self.complete, self.complete + self.partial + self.absent)

    @property
    def reported_share(self):
        return percent(self.reported, self.planned)


def per_person(stops, visits):
    """stops: (people, reported?) of the past plans; visits: (people, outcome) -> [PersonRow],
    most planned stops first."""
    rows = {}
    for people, reported in stops:
        row = rows.setdefault(people, PersonRow(people))
        row.planned += 1
        row.reported += 1 if reported else 0
    for people, outcome in visits:
        row = rows.setdefault(people, PersonRow(people))
        if outcome in OUTCOME_ORDER:
            setattr(row, outcome, getattr(row, outcome) + 1)
    return sorted(rows.values(), key=lambda r: (-r.planned, r.people.lower()))


def share_segments(parts):
    """[(key, label, n)] -> [(key, label, n, percent)] for a 100 % bar (no empty parts)."""
    total = sum(n for _, _, n in parts)
    return [(key, label, n, 100 * n / total) for key, label, n in parts if n] if total else []
