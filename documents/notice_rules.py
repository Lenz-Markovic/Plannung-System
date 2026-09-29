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
    return f"{text}, zwischen {window[0]:%H:%M} und {window[1]:%H:%M} Uhr" if window else text


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
