"""
First working day on which a person is still free (button "🗓 Erster freier Tag").

Pure function: dates in, a date out. "Free" = Monday to Friday, no plan (as
lead or in a team) and not absent.
"""

import datetime

HORIZON_DAYS = 180  # look at most half a year ahead


def first_free_day(start, busy_days, absences=(), horizon=HORIZON_DAYS):
    """start: first day to look at; busy_days: set of dates with a plan;
    absences: [(first day, last day)]. Returns a date or None (nothing free in the horizon)."""
    day = start
    for _ in range(horizon):
        if day.weekday() < 5 and day not in busy_days and not any(a <= day <= b for a, b in absences):
            return day
        day += datetime.timedelta(days=1)
    return None
