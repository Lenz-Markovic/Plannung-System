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
from dataclasses import dataclass

MAX_NET_MINUTES = 450         # 7.5 h
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


def confirmation_problems(day_plan, all_drives_from_tomtom):
    """Reasons why a plan cannot be confirmed (empty list = may be confirmed)."""
    problems = []
    if not all_drives_from_tomtom:
        problems.append("Ohne echte TomTom-Fahrzeiten kann der Plan nicht bestätigt werden.")
    if day_plan.over_limit_minutes:
        hours = f"{day_plan.over_limit_minutes / 60:.1f}".replace(".", ",")
        problems.append(f"Netto-Arbeitszeit {hours} h über 7,5 h – bitte Stopps herausnehmen.")
    return problems
