"""
Tenant notices ("Aushang"): time window, deadline, state. Pure functions.

Tenants get a window, not the exact minute: from the full hour before the
planned start to at least two hours later, e.g. a stop 09:25-10:05 becomes
"zwischen 09:00 und 11:00 Uhr". The notice should hang 14 days before the day
(many buildings say "Termin mindestens 14 Tage vorher bekanntgeben").
"""

import datetime
from dataclasses import dataclass

NOTICE_DAYS = 14          # hang the notice at least 14 days before
MIN_WINDOW_HOURS = 2      # the window is at least 2 hours

# states of the notice of one stop
MISSING = "missing"       # not printed yet, still in time
LATE = "late"             # not printed, and less than 14 days left
PRINTED = "printed"       # printed for the current day and window
OUTDATED = "outdated"     # printed, but the day or the window changed since


def notice_window(start, end):
    """(from, to) as datetime.time: full hour before `start` .. at least 2 h later, full hour."""
    if start is None:
        return None
    first = start.hour
    last_minute = (end.hour * 60 + end.minute) if end else first * 60
    last = max(first + MIN_WINDOW_HOURS, -(-last_minute // 60))  # round the end up to the full hour
    last = min(last, 23)
    return datetime.time(first, 0), datetime.time(last, 0)


def notice_text(date, window):
    """What the notice says about the date: 'Dienstag, 03.11.2026, zwischen 09:00 und 11:00 Uhr'."""
    days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    text = f"{days[date.weekday()]}, {date:%d.%m.%Y}"
    if window and window[0] and window[1]:
        return f"{text}, zwischen {window[0]:%H:%M} und {window[1]:%H:%M} Uhr"
    return f"{text}, ab {window[0]:%H:%M} Uhr" if window and window[0] else text


def notice_deadline(date):
    return date - datetime.timedelta(days=NOTICE_DAYS)


@dataclass(frozen=True)
class NoticeState:
    state: str
    deadline: datetime.date
    text: str             # e.g. "Dienstag, 03.11.2026, zwischen 09:00 und 11:00 Uhr"


def notice_state(date, window, printed_for, today):
    """printed_for: the text of the notice that was printed ("" = not printed yet)."""
    text = notice_text(date, window)
    deadline = notice_deadline(date)
    if printed_for:
        return NoticeState(PRINTED if printed_for == text else OUTDATED, deadline, text)
    return NoticeState(LATE if today > deadline else MISSING, deadline, text)


# --- for whom: the whole house (Aushang) or single flats (one Brief per flat) -------------------------

WHOLE_HOUSE, SOME_UNITS = "haus", "wohnungen"
SCOPES = [(WHOLE_HOUSE, "📄 Aushang ans ganze Haus"), (SOME_UNITS, "✉ Briefe an einzelne Wohnungen")]


def unit_list(units):
    """'Whg 3 (Müller), Whg 7;Whg 9' -> ['Whg 3 (Müller)', 'Whg 7', 'Whg 9'] - one Brief each."""
    parts = [" ".join(p.split()) for p in (units or "").replace(";", ",").replace("\n", ",").split(",")]
    return [p for p in parts if p]


def units_text(scope, units):
    """Line on the paper for single flats: 'Nur für: Whg 3 (Müller), Whg 7'."""
    flats = unit_list(units)
    return f"Nur für: {', '.join(flats)}" if scope == SOME_UNITS and flats else ""


def papers(scope, units):
    """What to print for one appointment: [''] = one Aushang for the house; or one Brief per flat."""
    flats = unit_list(units)
    return flats if scope == SOME_UNITS and flats else [""]


def effective_window(manual_from, manual_to, start, end):
    """(window, source): typed in by the office ("manual") wins, else from the plan times ("plan")."""
    if manual_from:
        return (manual_from, manual_to), "manual"
    window = notice_window(start, end)
    return window, ("plan" if window else "")


def parse_time(value):
    """'8', '8:30', '08.30' -> time; '' / broken -> None."""
    value = (value or "").strip().replace(".", ":")
    if not value:
        return None
    try:
        hours, _, minutes = value.partition(":")
        return datetime.time(int(hours), int(minutes or 0))
    except ValueError:
        return None


# --- 🗺 Aushang-Route: the order of the houses for the person who hangs them (printed list) ---------

AUSHANG_MINUTES = 5      # hang one Aushang in the house
BRIEF_MINUTES = 2        # one Brief at a flat door / letterbox


def stop_minutes(aushaenge, briefe):
    return aushaenge * AUSHANG_MINUTES + briefe * BRIEF_MINUTES


def route_order(points, start=None, distance=None):
    """Indexes of `points` ((lat, lon) or None) in driving order: nearest house next, from `start`
    (or from the first house), then shortened by swapping (2-opt). Houses without position at the end."""
    from planning.rules.drive_time import distance_km

    distance = distance or distance_km
    known = [i for i, p in enumerate(points) if p is not None]
    unknown = [i for i, p in enumerate(points) if p is None]
    if len(known) < 2:
        return known + unknown
    left = list(known)
    here = start if start is not None else points[left[0]]
    order = []
    while left:
        nxt = min(left, key=lambda i: distance(here, points[i]))
        order.append(nxt)
        left.remove(nxt)
        here = points[nxt]

    def length(seq):
        total = distance(start, points[seq[0]]) if start is not None else 0
        return total + sum(distance(points[a], points[b]) for a, b in zip(seq, seq[1:]))

    improved = True
    while improved:
        improved = False
        for i in range(len(order) - 1):
            for j in range(i + 1, len(order)):
                candidate = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
                if length(candidate) < length(order) - 1e-9:
                    order, improved = candidate, True
    return order + unknown


def route_times(start_minutes, drives, works):
    """Arrival / leave (minutes) per house: drives[i] = drive TO house i (0 for the first without start)."""
    times, t = [], start_minutes
    for drive, work in zip(drives, works):
        t += drive or 0
        times.append((t, t + work))
        t += work
    return times
