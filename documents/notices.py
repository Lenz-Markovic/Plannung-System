"""
Tenant notices (Aushang) for the stops of a plan: state, marking as printed.
The wording lives in templates/documents/aushang.html, the rules in notice_rules.py.
"""

from django.utils import timezone

from planning.models import StopKind

from .notice_rules import notice_state, notice_window


def notice_stops(stops):
    """Stops that need an own notice: readings and installations (a helper does not need one)."""
    return [stop for stop in stops if stop.kind != StopKind.HELP]


def state_of(stop, today=None):
    """NoticeState of one stop (missing / late / printed / outdated)."""
    window = notice_window(stop.start_time, stop.end_time)
    return notice_state(stop.tour.date, window, stop.notice_for, today or timezone.localdate())


def mark_printed(stops):
    """Remember that the notices were printed - for the day and window printed on them."""
    now = timezone.now()
    for stop in notice_stops(stops):
        stop.notice_printed_at = now
        stop.notice_for = state_of(stop).text
        stop.save(update_fields=["notice_printed_at", "notice_for", "updated_at"])
