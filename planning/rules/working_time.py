"""
Working-time rules of a day plan (spec section 7).

- Net working time = reading/installation + driving BETWEEN stops.
  The drive from home to the first stop (and back) does not count.
- At most 7.5 hours (450 min) net per day.
- 30 minutes break if the day is longer than 6 hours, placed after the
  first stop that ends at or after 12:00 (never after the last stop).

Port of the time calculation in planRechneTomTom() of the prototype, plus
the 6-hour condition from the spec.
"""

import datetime
import math
from dataclasses import dataclass

MAX_NET_MINUTES = 450         # 7.5 h
MIN_NET_MINUTES = 360         # below 6 h the day is "nicht ausgelastet" (only a notice)
BREAK_THRESHOLD_MINUTES = 360  # break only if the day is longer than 6 h
BREAK_NOT_BEFORE = datetime.time(12, 0)
DEFAULT_BREAK_MINUTES = 30


@dataclass(frozen=True)
class ScheduledStop:
    start: datetime.time
    end: datetime.time
    departure: datetime.time | None  # when the drive to the next stop starts
    break_after: bool


@dataclass(frozen=True)
class DayPlan:
    stops: list
    end: datetime.time
    work_minutes: int
    drive_minutes: int
    break_minutes: int
    break_after_index: int | None

    @property
    def net_minutes(self):
        return self.work_minutes + self.drive_minutes

    @property
    def over_limit_minutes(self):
        return max(0, self.net_minutes - MAX_NET_MINUTES)


def _to_minutes(value):
    return value.hour * 60 + value.minute


def _to_time(minutes):
    minutes = int(minutes) % (24 * 60)
    return datetime.time(minutes // 60, minutes % 60)


def needs_break(net_minutes):
    return net_minutes > BREAK_THRESHOLD_MINUTES


def schedule_day(start, work_minutes, drive_minutes, break_minutes=DEFAULT_BREAK_MINUTES):
    """Times of all stops of one day.

    start:          datetime.time the first stop begins
    work_minutes:   minutes per stop, e.g. [40, 30, 60]
    drive_minutes:  drive from stop i to stop i+1 (one less than the stops)
    break_minutes:  length of the break if one is needed (0 = never)
    """
    if len(drive_minutes) != max(0, len(work_minutes) - 1):
        raise ValueError("drive_minutes must have one entry less than work_minutes")
    net = sum(work_minutes) + sum(drive_minutes)
    wants_break = break_minutes > 0 and needs_break(net)

    t = _to_minutes(start)
    stops, break_index = [], None
    for i, minutes in enumerate(work_minutes):
        begin, end = t, t + minutes
        t = end
        is_last = i == len(work_minutes) - 1
        take_break = wants_break and break_index is None and not is_last and end >= _to_minutes(BREAK_NOT_BEFORE)
        if take_break:
            break_index = i
            t += break_minutes
        departure = None if is_last else _to_time(t)
        if not is_last:
            t += drive_minutes[i]
        stops.append(ScheduledStop(_to_time(begin), _to_time(end), departure, take_break))

    return DayPlan(
        stops=stops,
        end=_to_time(t),
        work_minutes=sum(work_minutes),
        drive_minutes=sum(drive_minutes),
        break_minutes=break_minutes if break_index is not None else 0,
        break_after_index=break_index,
    )


def _h(minutes):
    return f"{minutes / 60:.1f}".replace(".", ",")


@dataclass(frozen=True)
class TimeNotice:
    """Working time outside the normal range: the planner must approve it knowingly."""

    kind: str   # "over" or "under"
    text: str


def time_notice(day_plan):
    """More than 7,5 h or less than 6 h net - or None if the day is in the normal range."""
    net = day_plan.net_minutes
    if not net:
        return None
    if day_plan.over_limit_minutes:
        return TimeNotice("over", f"Netto-Arbeitszeit {_h(net)} h – {_h(day_plan.over_limit_minutes)} h über 7,5 h")
    if net < MIN_NET_MINUTES:
        return TimeNotice("under", f"Netto-Arbeitszeit nur {_h(net)} h – unter 6 h, der Tag ist nicht ausgelastet")
    return None


def confirmation_problems(day_plan, all_drives_from_tomtom, time_approved=False):
    """Reasons why a plan cannot be confirmed (empty list = may be confirmed).

    time_approved: the planner clicked "Arbeitszeit so übernehmen" - then a day
    over 7,5 h or under 6 h may be confirmed as it is.
    """
    problems = []
    if not all_drives_from_tomtom:
        problems.append("Ohne echte TomTom-Fahrzeiten kann der Plan nicht bestätigt werden.")
    notice = time_notice(day_plan)
    if notice and not time_approved:
        if notice.kind == "over":
            problems.append(f"Netto-Arbeitszeit {_h(day_plan.over_limit_minutes)} h über 7,5 h – "
                            "Stopps herausnehmen oder die Arbeitszeit bewusst so übernehmen.")
        else:
            problems.append(f"Netto-Arbeitszeit nur {_h(day_plan.net_minutes)} h – Stopps ergänzen "
                            "oder die Arbeitszeit bewusst so übernehmen.")
    return problems


def team_minutes(minutes, people, split=True):
    """Work time per stop when `people` work together: divided and rounded up to 5 min.

    team_minutes(240, 2) -> 120; team_minutes(50, 3) -> 20 (16,7 rounded up); one person or
    split=False -> unchanged.
    """
    if not split or people <= 1 or not minutes:
        return minutes
    return max(5, math.ceil(minutes / people / 5) * 5)
