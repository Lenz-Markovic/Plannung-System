"""
Tenant notices (Aushang) for the stops of a plan: optional per stop, state, marking as printed.
The page is the company template (documents/vorlage, aushang_fields.py), the rules in notice_rules.py.
"""

from django.utils import timezone

from buildings.models import WorkType
from planning.models import StopKind

from .aushang_fields import devices_from_categories, devices_from_counts, notice_fields
from .notice_rules import notice_state, notice_window


def notice_stops(stops):
    """Stops that need an own notice: readings and installations (a helper does not need one)."""
    return [stop for stop in stops if stop.kind != StopKind.HELP]


def wanted(stop):
    """Does this stop get a notice? Optional: switched on per stop ("＋ Aushang"; printing switches it on)."""
    return stop.kind != StopKind.HELP and stop.notice_wanted


def set_wanted(stop, on):
    stop.notice_wanted = bool(on)
    stop.save(update_fields=["notice_wanted", "updated_at"])


def fields_of(stop):
    """NoticeFields for the company template: number top left, address, boxes, weekday, date, time."""
    building, order = stop.building, stop.installation_order
    target = building or order
    number = building.file_number if building else (order.building_file_number or order.re_number)
    address = f"{target.street}, {target.zip_code} {target.city}".strip(", ")
    window = notice_window(stop.start_time, stop.end_time)
    if stop.kind == StopKind.READING:
        devices = devices_from_counts(building.hkv_count, building.wmz_count, building.wwz_count, building.kwz_count,
                                      building.rwm_count, building.hwmz_count, building.has_rwm)
        access = building.access
        rwm_check = bool(access and "RWM-Prüfung in den Wohnungen" in access.reasons)
        return notice_fields("reading", stop.tour.date, window, number, address, devices, rwm_check=rwm_check)
    codes = [item.category.code for item in order.items.all() if item.category]
    return notice_fields("installation", stop.tour.date, window, number, address, devices_from_categories(codes),
                         exchange=order.work_type == WorkType.EXCHANGE)


def state_of(stop, today=None):
    """NoticeState of one stop (missing / late / printed / outdated)."""
    window = notice_window(stop.start_time, stop.end_time)
    return notice_state(stop.tour.date, window, stop.notice_for, today or timezone.localdate())


def mark_printed(stops, user=None):
    """Remember that the notices were printed (and by whom) - for the day and window printed on them."""
    now = timezone.now()
    by = user if user is not None and user.is_authenticated else None
    for stop in notice_stops(stops):
        stop.notice_printed_at, stop.notice_wanted, stop.notice_printed_by = now, True, by
        stop.notice_for = state_of(stop).text
        stop.save(update_fields=["notice_printed_at", "notice_for", "notice_wanted", "notice_printed_by", "updated_at"])
