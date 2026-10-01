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
    """What the notice says about the date: 'Dienstag, 03.11.2026, zwischen 09:00 und 11:00 Uhr'
    (only a start: 'ab 09:00 Uhr')."""
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


# --- how the tenants are told (Ankündigung) --------------------------------------------------------
# Only "Aushang durch uns" needs a trip to the house (📄 Aushang-Fahrt, planned like a reading).

AUSHANG, BRIEF, MAIL_HV, AUSHANG_HV, PHONE, OTHER = "aushang", "brief", "mail_hv", "aushang_hv", "telefon", "sonstiges"
CHANNELS = [
    (AUSHANG, "📄 Aushang durch uns"),
    (BRIEF, "✉ Brief von uns"),
    (MAIL_HV, "📧 Mail an Hausverwaltung"),
    (AUSHANG_HV, "🏢 Aushang durch Hausverwaltung"),
    (PHONE, "📞 telefonisch mit Mieter"),
    (OTHER, "Sonstiges"),
]
CHANNEL_LABELS = dict(CHANNELS)
SENT_LABELS = {  # what "✓ erledigt" means for each way
    AUSHANG: "aufgehängt", BRIEF: "Brief verschickt", MAIL_HV: "Mail verschickt",
    AUSHANG_HV: "an Hausverwaltung geschickt", PHONE: "Mieter informiert", OTHER: "erledigt",
}
WHOLE_HOUSE, SOME_UNITS = "haus", "wohnungen"
SCOPES = [(WHOLE_HOUSE, "ganzes Haus"), (SOME_UNITS, "nur bestimmte Wohnungen")]

# announcement states (one per appointment)
A_OPEN = "open"               # nobody decided yet how the tenants are told
A_PRINT = "print"             # Aushang/Brief: not printed yet
A_TRIP = "trip"               # Aushang durch uns: printed, no trip to the house planned
A_TRIP_PLANNED = "trip_planned"
A_SEND = "send"               # Brief / Mail / HV / phone: still to send
A_DONE = "done"               # aufgehängt / verschickt / informiert
A_LABELS = {
    A_OPEN: "❔ Ankündigung offen", A_PRINT: "🖨 noch drucken", A_TRIP: "🚗 Aushang-Fahrt planen",
    A_TRIP_PLANNED: "📅 Aushang-Fahrt geplant", A_SEND: "✉ noch verschicken", A_DONE: "✓ angekündigt",
}
A_OPEN_STATES = frozenset({A_OPEN, A_PRINT, A_TRIP, A_SEND})
PRINTED_CHANNELS = frozenset({AUSHANG, BRIEF, AUSHANG_HV})   # a paper is made from the template
DEFAULT_TRIP_MINUTES = 10     # hanging one notice


def announce_state(channel, printed, sent, trip_planned):
    """What is still to do so the tenants know about the appointment."""
    if sent:
        return A_DONE
    if not channel:
        return A_OPEN
    if channel in PRINTED_CHANNELS and not printed:
        return A_PRINT
    if channel == AUSHANG:
        return A_TRIP_PLANNED if trip_planned else A_TRIP
    return A_SEND


def needs_trip(channel):
    return channel == AUSHANG


def trip_hint(trip_date, appointment_date):
    """'' when the trip is in time; else why not (the notice should hang 14 days before)."""
    if trip_date is None or appointment_date is None:
        return ""
    if trip_date >= appointment_date:
        return "die Aushang-Fahrt ist erst am Termintag oder danach"
    if trip_date > notice_deadline(appointment_date):
        return f"Aushang hängt nur {(appointment_date - trip_date).days} Tage vorher (Frist: {NOTICE_DAYS} Tage)"
    return ""


def units_text(scope, units):
    """Line on the paper when it is only for some flats: 'Nur für: Whg 3 (Müller), Whg 7'."""
    units = " ".join((units or "").split())
    return f"Nur für: {units}" if scope == SOME_UNITS and units else ""


def effective_window(manual_from, manual_to, start, end):
    """(from, to, source): the window typed in by the office wins, else from the plan times."""
    if manual_from:
        return (manual_from, manual_to), "manual"
    window = notice_window(start, end)
    return window, ("plan" if window else "")


def parse_time(value):
    """'8', '8:30', '08:30' -> time; '' / broken -> None."""
    value = (value or "").strip().replace(".", ":")
    if not value:
        return None
    try:
        hours, _, minutes = value.partition(":")
        return datetime.time(int(hours), int(minutes or 0))
    except ValueError:
        return None


# --- the page "📄 Aushänge & Ankündigungen": filters ----------------------------------------------

A_FILTERS = [("offen", "alles Offene"), (A_OPEN, "❔ Ankündigung offen"), (A_PRINT, "🖨 noch drucken"),
             (A_TRIP, "🚗 Fahrt planen"), (A_TRIP_PLANNED, "📅 Fahrt geplant"), (A_SEND, "✉ noch verschicken"),
             (A_DONE, "✓ angekündigt"), ("alle", "alle")]
A_FILTER_KEYS = [key for key, _ in A_FILTERS]
HORIZONS = [("14", "nächste 2 Wochen"), ("28", "nächste 4 Wochen"), ("56", "nächste 8 Wochen"), ("alle", "alle geplanten")]


def chosen_a_filter(value):
    return value if value in A_FILTER_KEYS else "offen"


def chosen_horizon(value):
    return value if value in dict(HORIZONS) else "28"


def in_a_filter(chosen, state):
    if chosen == "alle":
        return True
    if chosen == "offen":
        return state in A_OPEN_STATES
    return state == chosen


def count_a_filters(states):
    states = list(states)
    return {key: sum(1 for s in states if in_a_filter(key, s)) for key in A_FILTER_KEYS}


def is_late(deadline, today, state):
    """Still not announced although the 14-day deadline is over."""
    return state != A_DONE and today > deadline
