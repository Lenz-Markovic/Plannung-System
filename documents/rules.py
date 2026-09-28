"""
14-day deadline for received cost documents (spec section 7).

"Nach dem Eingang der Unterlagen muss die Liegenschaft innerhalb von 14 Tagen
freigegeben sein. Danach erscheint eine Warnung, die sich nicht einfach
wegklicken lässt. Sie verschwindet erst bei einem neuen Termin oder einem
Statuswechsel, dann beginnt eine neue Frist von 14 Tagen, bis die
Liegenschaft freigegeben ist."

"Rot heißt: kein Termin vorhanden. Gelb heißt: Termin vorhanden, prüfen,
ob er vorgezogen werden muss."

Ports of prioSync() and ulInfo() from the prototype. Pure functions:
plain values in, plain values out.
"""

import datetime
from dataclasses import dataclass

DEADLINE_DAYS = 14
SOON_DAYS = 3  # "bald": 3 days or less left

# states
RELEASED = "released"   # freigegeben: no deadline any more
OVERDUE = "overdue"     # Frist abgelaufen -> warning
SOON = "soon"           # <= 3 days left
OK = "ok"

# reasons why a deadline started again
RESET_STATUS = "status"
RESET_APPOINTMENT = "appointment"


@dataclass(frozen=True)
class DeadlineTracking:
    """What we remember about a building's deadline (fields of CostDocumentReceipt)."""

    deadline_start: datetime.date
    reset_reason: str
    last_seen_status: str
    last_seen_planned_date: datetime.date | None


def track_deadline(tracking, status, planned_date, today):
    """Restart the deadline when the status changed or a NEW appointment exists.

    Returns the (possibly) updated tracking. Removing an appointment does not
    restart the deadline - only a new date does (as in the prototype).
    """
    new_appointment = planned_date is not None and planned_date != tracking.last_seen_planned_date
    status_changed = status != tracking.last_seen_status
    if not (status_changed or new_appointment):
        return tracking
    return DeadlineTracking(
        deadline_start=today,
        reset_reason=RESET_STATUS if status_changed else RESET_APPOINTMENT,
        last_seen_status=status,
        last_seen_planned_date=planned_date,
    )


@dataclass(frozen=True)
class DeadlineInfo:
    state: str
    deadline_end: datetime.date
    days_left: int  # negative = days overdue

    @property
    def days_overdue(self):
        return max(0, -self.days_left)


def deadline_info(deadline_start, is_released, today):
    deadline_end = deadline_start + datetime.timedelta(days=DEADLINE_DAYS)
    days_left = (deadline_end - today).days
    if is_released:
        state = RELEASED
    elif days_left <= 0:
        state = OVERDUE
    elif days_left <= SOON_DAYS:
        state = SOON
    else:
        state = OK
    return DeadlineInfo(state=state, deadline_end=deadline_end, days_left=days_left)


def warning_colour(planned_date):
    """Red = no appointment yet, yellow = appointment exists (pull it forward?)."""
    return "yellow" if planned_date else "red"


# "Später erinnern": the user may put the warning aside, but it always comes back.
SNOOZE_CHOICES = {
    "15": "in 15 Minuten",
    "60": "in 1 Stunde",
    "morgen": "morgen früh",
}
MORNING = datetime.time(7, 30)


def remind_again_at(choice, now):
    """When the warning comes back after 'Später erinnern'.

    now is a timezone-aware local datetime. Unknown choices use 15 minutes,
    so the warning can never be switched off for good.
    """
    if choice == "morgen":
        tomorrow = (now + datetime.timedelta(days=1)).date()
        return now.replace(year=tomorrow.year, month=tomorrow.month, day=tomorrow.day,
                           hour=MORNING.hour, minute=MORNING.minute, second=0, microsecond=0)
    minutes = 60 if choice == "60" else 15
    return now + datetime.timedelta(minutes=minutes)
