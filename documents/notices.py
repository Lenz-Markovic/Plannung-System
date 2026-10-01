"""
Tenant notices (Aushang) for the stops of a plan: optional per stop, state, marking as printed.
The page is the company template (documents/vorlage, aushang_fields.py), the rules in notice_rules.py.
"""

import datetime

from django.utils import timezone

from buildings.models import WorkType
from planning.models import StopKind

from . import notice_rules as rules
from .aushang_fields import devices_from_categories, devices_from_counts, notice_fields
from .notice_rules import notice_state


def notice_stops(stops):
    """Stops that need an own notice: readings and installations (a helper does not need one)."""
    return [stop for stop in stops if stop.kind != StopKind.HELP]


def wanted(stop):
    """Does this stop get a notice? Optional: switched on per stop ("＋ Aushang"; printing switches it on)."""
    return stop.kind != StopKind.HELP and stop.notice_wanted


def set_wanted(stop, on):
    """＋ Aushang / ✕ kein Aushang (old buttons): also the decision of the Terminierung."""
    stop.notice_wanted = bool(on)
    if on:
        stop.notice_choice = rules.BY_LETTERS if stop.notice_scope == rules.SOME_UNITS else rules.BY_AUSHANG
    elif stop.notice_choice in rules.PRINTED_CHOICES or not stop.notice_choice:
        stop.notice_choice = rules.NOT_NEEDED
    stop.save(update_fields=["notice_wanted", "notice_choice", "updated_at"])


def _record(user, stop, text):
    from journal.activity import day_label, record
    from journal.models import ActivityKind

    target = stop.building or stop.installation_order
    record(user, ActivityKind.NOTICE, f"{text} – {target.street} ({stop.tour.employee} {day_label(stop.tour.date)})",
           tour=stop.tour, building=stop.building, order=stop.installation_order)


def set_choice(stop, user, choice, units=None):
    """The Terminierung decides: 📄 Aushang / ✉ Briefe / ☎ telefonisch / 📧 per Mail / – keine.
    Aushang and Briefe go to printing; Briefe need the flats (taken from Zugang if empty)."""
    from django.core.exceptions import ValidationError

    if units is not None:
        units = ", ".join(rules.unit_list(units))[:300]
    flats = units if units else (stop.notice_units or (stop.access_units if stop.access_choice == rules.ACCESS_SOME else ""))
    problems = rules.choice_problems(choice, flats)
    if problems:
        raise ValidationError(problems[0])
    stop.notice_choice = choice
    stop.notice_wanted = choice in rules.PRINTED_CHOICES
    if choice == rules.BY_LETTERS:
        stop.notice_scope, stop.notice_units = rules.SOME_UNITS, flats
    elif choice == rules.BY_AUSHANG:
        stop.notice_scope = rules.WHOLE_HOUSE
    stop.save(update_fields=["notice_choice", "notice_wanted", "notice_scope", "notice_units", "updated_at"])
    label = dict(rules.CHOICES)[choice]
    _record(user, stop, f"Ankündigung: {label}" + (f" ({stop.notice_units})" if choice == rules.BY_LETTERS else ""))
    return stop


def set_access(stop, user, scope, units=""):
    """Zugang: in alle Wohnungen / nur in diese Wohnungen / nicht in die Wohnungen (the planner decides
    when the system did not recognise it)."""
    from django.core.exceptions import ValidationError

    if scope not in dict(rules.ACCESS_SCOPES):
        raise ValidationError("Unbekannter Zugang.")
    units = ", ".join(rules.unit_list(units))[:300]
    if scope == rules.ACCESS_SOME and not units:
        raise ValidationError("Bitte die Wohnungen eintragen (z. B. Whg 3, Whg 7).")
    stop.access_scope, stop.access_units = scope, units if scope == rules.ACCESS_SOME else ""
    fields = ["access_scope", "access_units", "updated_at"]
    if scope == rules.ACCESS_SOME and stop.notice_choice == rules.BY_LETTERS and not stop.notice_printed_at:
        stop.notice_units = stop.access_units      # the Briefe go to the same flats
        fields.append("notice_units")
    stop.save(update_fields=fields)
    _record(user, stop, f"Zugang: {stop.access_text}")
    return stop


def estimated_times(tour):
    """{stop pk: (start, end)} for a plan WITHOUT saved times (e.g. imported): worked out from its
    start, the work minutes and ~15 min drive (the same estimate as 🛰 Wer ist wo?)."""
    from planning.whereabouts import _plan

    stops = sorted(tour.stops.all(), key=lambda s: s.position)
    if not stops or all(s.start_time for s in stops):
        return {}
    plan, _ = _plan(tour, stops)
    return {s.pk: (datetime.time(min(p.start // 60, 23), p.start % 60), datetime.time(min(p.end // 60, 23), p.end % 60))
            for s, p in zip(stops, plan)}


def window_of(stop, estimates=None):
    """(window, source): typed in by hand ("manual"), from the plan times ("plan") or estimated ("estimate")."""
    window, source = rules.effective_window(stop.notice_from, stop.notice_to, stop.start_time, stop.end_time)
    if window is None:
        estimates = estimated_times(stop.tour) if estimates is None else estimates
        if stop.pk in estimates:
            window, source = rules.notice_window(*estimates[stop.pk]), "estimate"
    return window, source


def time_label(window):
    if not window:
        return ""
    return f"{window[0]:%H:%M}–{window[1]:%H:%M} Uhr" if window[1] else f"ab {window[0]:%H:%M} Uhr"


def fields_of(stop, flat="", estimates=None):
    """NoticeFields for the company template: number top left, address, boxes, weekday, date, time.
    flat: a Brief for one flat ("Für: Whg 3 (Müller)" at the bottom)."""
    building, order = stop.building, stop.installation_order
    target = building or order
    number = building.file_number if building else (order.building_file_number or order.re_number)
    address = f"{target.street}, {target.zip_code} {target.city}".strip(", ")
    window, _ = window_of(stop, estimates)
    if stop.kind == StopKind.READING:
        devices = devices_from_counts(building.hkv_count, building.wmz_count, building.wwz_count, building.kwz_count,
                                      building.rwm_count, building.hwmz_count, building.has_rwm)
        access = building.access
        rwm_check = bool(access and "RWM-Prüfung in den Wohnungen" in access.reasons)
        fields = notice_fields("reading", stop.tour.date, window, number, address, devices, rwm_check=rwm_check)
    else:
        codes = [item.category.code for item in order.items.all() if item.category]
        fields = notice_fields("installation", stop.tour.date, window, number, address, devices_from_categories(codes),
                               exchange=order.work_type == WorkType.EXCHANGE)
    if flat:
        fields.bottom = f"Für: {flat}"
    return fields


def pages_of(stop, estimates=None):
    """[(flat, NoticeFields)]: one Aushang for the house, or one Brief per flat."""
    return [(flat, fields_of(stop, flat, estimates)) for flat in rules.papers(stop.notice_scope, stop.notice_units)]


def state_of(stop, today=None, estimates=None):
    """NoticeState of one stop (missing / late / printed / outdated)."""
    window, _ = window_of(stop, estimates)
    return notice_state(stop.tour.date, window, stop.notice_for, today or timezone.localdate())


def set_notice(stop, user, scope=None, units=None, window_from=None, window_to=None):
    """✎ Aushang ans Haus / Briefe an einzelne Wohnungen, and the time by hand. Returns what changed."""
    from journal.activity import day_label, record
    from journal.models import ActivityKind

    changes = []
    if scope in dict(rules.SCOPES) and scope != stop.notice_scope:
        stop.notice_scope = scope
        changes.append(dict(rules.SCOPES)[scope])
    if units is not None and ", ".join(rules.unit_list(units))[:300] != stop.notice_units:
        stop.notice_units = ", ".join(rules.unit_list(units))[:300]
        changes.append(f"Wohnungen: {stop.notice_units or '–'}")
    if window_from is not None:
        start, end = rules.parse_time(window_from), rules.parse_time(window_to)
        end = end if start else None
        if (start, end) != (stop.notice_from, stop.notice_to):
            stop.notice_from, stop.notice_to = start, end
            changes.append(f"Zeit {time_label((start, end))}" if start else "Zeit wieder aus dem Fahrplan")
    if changes:
        stop.notice_wanted = True
        stop.notice_choice = rules.BY_LETTERS if stop.notice_scope == rules.SOME_UNITS else rules.BY_AUSHANG
        stop.save(update_fields=["notice_scope", "notice_units", "notice_from", "notice_to", "notice_wanted",
                                 "notice_choice", "updated_at"])
        target = stop.building or stop.installation_order
        record(user, ActivityKind.NOTICE, f"Aushang: {', '.join(changes)} – {target.street} ({stop.tour.employee} "
               f"{day_label(stop.tour.date)})", tour=stop.tour, building=stop.building, order=stop.installation_order)
    return changes


def mark_printed(stops, user=None):
    """Remember that the notices were printed (and by whom) - for the day and window printed on them."""
    now = timezone.now()
    by = user if user is not None and user.is_authenticated else None
    for stop in notice_stops(stops):
        stop.notice_printed_at, stop.notice_wanted, stop.notice_printed_by = now, True, by
        if stop.notice_choice not in rules.PRINTED_CHOICES:   # printed = it is an Aushang / Briefe
            stop.notice_choice = rules.BY_LETTERS if stop.notice_scope == rules.SOME_UNITS else rules.BY_AUSHANG
        stop.notice_for = state_of(stop).text
        stop.save(update_fields=["notice_printed_at", "notice_for", "notice_wanted", "notice_printed_by",
                                 "notice_choice", "updated_at"])
