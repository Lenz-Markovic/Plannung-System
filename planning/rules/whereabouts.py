"""
🛰 Wer ist wo? - where each person should be at a given moment according to their Fahrplan
(pure functions, no database). This is NOT GPS: it only reads the planned times.

A day of one plan:  [Anfahrt] → Stopp 1 (von–bis) → [Pause] → Fahrt → Stopp 2 … → [Heimfahrt]
The times come from rules/working_time.py: departure = end of the stop (+ the break after it),
the next stop begins after the drive (or later for a fixed appointment: then the person waits).
"""

from dataclasses import dataclass

BEFORE, COMMUTE, AT, PAUSE, DRIVING, WAITING, HOME, DONE = (
    "before", "commute", "at", "pause", "driving", "waiting", "home", "done")
STATE_LABELS = {
    BEFORE: "⏳ noch nicht unterwegs",
    COMMUTE: "🚗 Anfahrt",
    AT: "📍 vor Ort",
    PAUSE: "☕ Pause",
    DRIVING: "🚗 unterwegs",
    WAITING: "⏱ wartet auf Termin",
    HOME: "🏠 Heimfahrt",
    DONE: "✓ Feierabend",
}
STATE_ORDER = [AT, DRIVING, PAUSE, WAITING, COMMUTE, BEFORE, HOME, DONE]
DAY_START, DAY_END = 6 * 60, 20 * 60     # the time slider: 06:00-20:00
BEHIND_TOLERANCE = 30                    # minutes: plan says stop n is over, but nothing reported
ESTIMATED_DRIVE = 15                     # minutes between two stops when a plan has no drive times


@dataclass(frozen=True)
class PlanStop:
    """One stop of a plan: minutes since midnight, point = (lat, lon) or None."""
    n: int
    start: int
    end: int
    departure: int | None
    drive: int | None
    point: tuple | None
    label: str = ""
    reported: bool = False


@dataclass(frozen=True)
class Where:
    state: str
    stop: int | None           # index into the stops (at / pause / waiting / the stop driven FROM)
    next_stop: int | None      # index of the stop driven TO
    fraction: float            # 0..1 of the drive (for the position on the way)
    since: int | None          # minutes
    until: int | None
    point: tuple | None


def minutes(value):
    """datetime.time -> minutes since midnight (None stays None)."""
    return None if value is None else value.hour * 60 + value.minute


def clock(value):
    return "" if value is None else f"{value // 60:02d}:{value % 60:02d}"


def between(a, b, fraction):
    """A point on the straight line from a to b (the map shows a plan, not the real road)."""
    if a is None or b is None:
        return a or b
    fraction = min(1.0, max(0.0, fraction))
    return (a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction)


def where_at(stops, t, commute_to=None, commute_from=None):
    """Where the plan puts the person at minute t. stops: PlanStop in order (at least one)."""
    if not stops:
        return None
    first, last = stops[0], stops[-1]
    if t < first.start:
        if commute_to and t >= first.start - commute_to:
            return Where(COMMUTE, 0, None, 0.0, first.start - commute_to, first.start, first.point)
        return Where(BEFORE, 0, None, 0.0, None, first.start, first.point)
    for i, stop in enumerate(stops):
        if stop.start <= t < stop.end:
            return Where(AT, i, None, 0.0, stop.start, stop.end, stop.point)
        if i == len(stops) - 1:
            break
        following = stops[i + 1]
        departure = stop.departure if stop.departure is not None else stop.end
        departure = min(max(departure, stop.end), following.start)
        if stop.end <= t < departure:
            return Where(PAUSE, i, None, 0.0, stop.end, departure, stop.point)
        arrival = following.start
        if stop.drive is not None:
            arrival = min(following.start, departure + stop.drive)
        if departure <= t < arrival:
            fraction = (t - departure) / (arrival - departure) if arrival > departure else 1.0
            return Where(DRIVING, i, i + 1, fraction, departure, arrival, between(stop.point, following.point, fraction))
        if arrival <= t < following.start:
            return Where(WAITING, i + 1, None, 0.0, arrival, following.start, following.point)
    if commute_from and t < last.end + commute_from:
        return Where(HOME, len(stops) - 1, None, 0.0, last.end, last.end + commute_from, last.point)
    return Where(DONE, len(stops) - 1, None, 0.0, last.end, None, last.point)


def behind_plan(stops, where, t, tolerance=BEHIND_TOLERANCE):
    """Stops the plan says are over for more than `tolerance` minutes but nobody reported yet
    (a hint for the office to call - nothing more). Returns the numbers of those stops."""
    return [s.n for s in stops if s.end + tolerance <= t and not s.reported]


def describe(where, stops):
    """One line for the list: 'vor Ort: Stopp 2 · Hauptstr. 5 · bis 10:40'."""
    if where is None:
        return ""
    stop = stops[where.stop] if where.stop is not None else None
    if where.state == AT:
        return f"Stopp {stop.n} · {stop.label} · bis {clock(where.until)}"
    if where.state == DRIVING:
        target = stops[where.next_stop]
        return f"von Stopp {stop.n} nach Stopp {target.n} · {target.label} · an {clock(where.until)}"
    if where.state == PAUSE:
        return f"nach Stopp {stop.n} · {stop.label} · bis {clock(where.until)}"
    if where.state == WAITING:
        return f"an Stopp {stop.n} · {stop.label} · Termin um {clock(where.until)}"
    if where.state == COMMUTE:
        return f"zu Stopp 1 · {stop.label} · an {clock(where.until)}"
    if where.state == BEFORE:
        return f"beginnt um {clock(where.until)} · Stopp 1 · {stop.label}"
    if where.state == HOME:
        return f"nach dem letzten Stopp {stop.n} · zu Hause ca. {clock(where.until)}"
    return f"letzter Stopp {stop.n} · {stop.label} · fertig um {clock(where.since)}"


def count_states(states):
    found = {state: 0 for state in STATE_ORDER}
    for state in states:
        found[state] = found.get(state, 0) + 1
    return found


def clamp_time(value, default):
    """'HH:MM' from the page -> minutes, inside the slider range; bad input -> default."""
    try:
        hours, mins = str(value).split(":")
        t = int(hours) * 60 + int(mins)
    except (ValueError, TypeError):
        return default
    if not 0 <= int(mins) < 60:
        return default
    return min(max(t, 0), 24 * 60 - 1)
