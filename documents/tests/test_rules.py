import datetime

import pytest

from documents.rules import (
    OK, OVERDUE, RELEASED, RESET_APPOINTMENT, RESET_STATUS, SOON,
    DeadlineTracking, deadline_info, track_deadline, warning_colour,
)

def SEP(day):
    """A date in September 2026 (short helper for the tests)."""
    return datetime.date(2026, 9, day)


@pytest.mark.parametrize(
    "today, released, state, days_left",
    [
        (SEP(1), False, OK, 14),        # received today
        (SEP(11), False, OK, 4),
        (SEP(12), False, SOON, 3),      # 3 days left -> "bald"
        (SEP(15), False, OVERDUE, 0),   # day 14: deadline reached -> warning
        (SEP(28), False, OVERDUE, -13), # the demo data: "seit 13 Tagen überfällig"
        (SEP(28), True, RELEASED, -13), # released: never a warning
    ],
)
def test_deadline_info(today, released, state, days_left):
    info = deadline_info(SEP(1), released, today)
    assert (info.state, info.days_left) == (state, days_left)
    assert info.deadline_end == SEP(15)


def tracking(status="open", planned=None):
    return DeadlineTracking(deadline_start=SEP(1), reset_reason="", last_seen_status=status, last_seen_planned_date=planned)


def test_nothing_changed_keeps_deadline():
    assert track_deadline(tracking(), "open", None, SEP(28)) == tracking()


def test_status_change_starts_new_deadline():
    new = track_deadline(tracking(), "rework", None, SEP(28))
    assert (new.deadline_start, new.reset_reason, new.last_seen_status) == (SEP(28), RESET_STATUS, "rework")
    assert deadline_info(new.deadline_start, False, SEP(28)).state == OK  # warning is gone


def test_new_appointment_starts_new_deadline():
    new = track_deadline(tracking(), "open", datetime.date(2026, 12, 3), SEP(28))
    assert (new.deadline_start, new.reset_reason) == (SEP(28), RESET_APPOINTMENT)


def test_removing_the_appointment_does_not_restart():
    old = tracking(planned=datetime.date(2026, 12, 3))
    assert track_deadline(old, "open", None, SEP(28)) == old


def test_warning_colour():
    assert warning_colour(None) == "red"
    assert warning_colour(datetime.date(2026, 12, 3)) == "yellow"
