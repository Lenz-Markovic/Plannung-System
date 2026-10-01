"""
Tenant notices (Aushang) for the stops of a plan: optional per stop, state, marking as printed.
The page is the company template (documents/vorlage, aushang_fields.py), the rules in notice_rules.py.
"""

import datetime

from django.utils import timezone

from buildings.models import WorkType
from planning.models import StopKind, TourStop

from . import notice_rules as rules
from .aushang_fields import devices_from_categories, devices_from_counts, notice_fields
from .notice_rules import notice_state

APPOINTMENT_KINDS = (StopKind.READING, StopKind.INSTALLATION)


def notice_stops(stops):
    """Stops that need an own notice: readings and installations (not a helper, not the Aushang-Fahrt itself)."""
    return [stop for stop in stops if stop.kind in APPOINTMENT_KINDS]


def wanted(stop):
    """Does this stop get a notice? Optional: switched on per stop ("＋ Aushang"; printing switches it on)."""
    return stop.kind in APPOINTMENT_KINDS and stop.notice_wanted


# --- the time on the notice -----------------------------------------------------------------------

def estimated_times(tour):
    """{stop pk: (start, end)} for a plan WITHOUT saved times (e.g. imported): worked out from its
    start, the work minutes and ~15 min drive (the same estimate as 🛰 Wer ist wo?)."""
    from planning.whereabouts import _plan

    stops = sorted(tour.stops.all(), key=lambda s: s.position)
    if not stops or all(s.start_time for s in stops):
        return {}
    plan, _ = _plan(tour, stops)
    clock = lambda m: datetime.time(min(m // 60, 23), m % 60)  # noqa: E731
    return {s.pk: (clock(p.start), clock(p.end)) for s, p in zip(stops, plan)}


def window_of(stop, estimates=None):
    """(window, source): typed in by hand ("manual"), from the plan times ("plan"), estimated ("estimate")."""
    window, source = rules.effective_window(stop.notice_from, stop.notice_to, stop.start_time, stop.end_time)
    if window is None:
        estimates = estimated_times(stop.tour) if estimates is None else estimates
        if stop.pk in estimates:
            window, source = rules.notice_window(*estimates[stop.pk]), "estimate"
    return window, source


def set_wanted(stop, on):
    stop.notice_wanted = bool(on)
    stop.save(update_fields=["notice_wanted", "updated_at"])


def fields_of(stop):
    """NoticeFields for the company template: number top left, address, boxes, weekday, date, time."""
    building, order = stop.building, stop.installation_order
    target = building or order
    number = building.file_number if building else (order.building_file_number or order.re_number)
    address = f"{target.street}, {target.zip_code} {target.city}".strip(", ")
    window, _ = window_of(stop)
    bottom = rules.units_text(stop.notice_scope, stop.notice_units)
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
    fields.bottom = bottom  # "Nur für: Whg 3, Whg 7" when it is only for some flats
    return fields


def state_of(stop, today=None, estimates=None):
    """NoticeState of one stop (missing / late / printed / outdated)."""
    window, _ = window_of(stop, estimates)
    return notice_state(stop.tour.date, window, stop.notice_for, today or timezone.localdate())


def mark_printed(stops, user=None):
    """Remember that the notices were printed (and by whom) - for the day and window printed on them."""
    now = timezone.now()
    by = user if user is not None and user.is_authenticated else None
    for stop in notice_stops(stops):
        stop.notice_printed_at, stop.notice_wanted, stop.notice_printed_by = now, True, by
        stop.notice_for = state_of(stop).text
        stop.save(update_fields=["notice_printed_at", "notice_for", "notice_wanted", "notice_printed_by", "updated_at"])


# --- Ankündigung: how the tenants are told, and the 📄 Aushang-Fahrt -------------------------------

def _object_filter(stop):
    if stop.kind == StopKind.INSTALLATION or (stop.kind == StopKind.NOTICE and stop.installation_order_id):
        return {"installation_order_id": stop.installation_order_id}
    return {"building_id": stop.building_id, "installation_order__isnull": True}


def trip_of(stop):
    """The 📄 Aushang-Fahrt for this appointment: the latest one before it at the same object (or None)."""
    return (TourStop.objects.filter(kind=StopKind.NOTICE, tour__date__lte=stop.tour.date, **_object_filter(stop))
            .select_related("tour__employee").order_by("-tour__date").first())


def appointment_of(trip):
    """The appointment an Aushang-Fahrt is for: the first open reading/installation at the object after it."""
    kind = StopKind.INSTALLATION if trip.installation_order_id else StopKind.READING
    return (TourStop.objects.filter(kind=kind, tour__date__gte=trip.tour.date, done_at__isnull=True, outcome="",
                                    **_object_filter(trip))
            .select_related("tour__employee", "building", "installation_order").order_by("tour__date").first())


def announce(stop, trip="?"):
    """(state, label) of the Ankündigung of one appointment."""
    trip = trip_of(stop) if trip == "?" else trip
    state = rules.announce_state(stop.notice_channel, bool(stop.notice_printed_at), bool(stop.notice_sent_at),
                                 trip is not None)
    return state, rules.A_LABELS[state]


def trip_targets(pairs):
    """The ticked appointments ((building id, order id)) for the plan dialog: what, where, when."""
    found = []
    for building_id, order_id in pairs:
        kind = StopKind.INSTALLATION if order_id else StopKind.READING
        query = {"installation_order_id": order_id} if order_id else {"building_id": building_id}
        stop = (TourStop.objects.filter(kind=kind, done_at__isnull=True, outcome="", **query)
                .select_related("tour", "building", "installation_order").order_by("tour__date").first())
        if stop is None:
            continue
        target = stop.building or stop.installation_order
        found.append({
            "building_id": building_id if not order_id else None, "order_id": order_id or None, "stop": stop,
            "ref": f"AZ {stop.building.file_number}" if kind == StopKind.READING else f"RE {stop.installation_order.re_number}",
            "street": target.street, "city": target.city, "date": stop.tour.date,
            "what": "Ablesung" if kind == StopKind.READING else "Montage",
            "units": rules.units_text(stop.notice_scope, stop.notice_units), "minutes": rules.DEFAULT_TRIP_MINUTES,
        })
    return found


def _label(stop):
    target = stop.building or stop.installation_order
    ref = f"AZ {stop.building.file_number}" if stop.kind == StopKind.READING else f"RE {stop.installation_order.re_number}"
    return f"{ref} {target.street} (Termin {stop.tour.date:%d.%m.%Y})"


def _record(user, stop, text):
    from journal.activity import record
    from journal.models import ActivityKind

    record(user, ActivityKind.NOTICE, f"{text}: {_label(stop)}", tour=stop.tour, building=stop.building,
           order=stop.installation_order)


def set_announcement(stop, user, channel=None, scope=None, units=None, window_from="", window_to=""):
    """Save how the tenants are told (+ for whom, + the time window by hand). Returns what changed."""
    changes = []
    if channel is not None and channel in ("", *rules.CHANNEL_LABELS) and channel != stop.notice_channel:
        stop.notice_channel = channel
        if channel and channel in rules.PRINTED_CHANNELS:
            stop.notice_wanted = True
        changes.append(f"Ankündigung: {rules.CHANNEL_LABELS.get(channel, 'offen')}")
    if scope is not None and scope in dict(rules.SCOPES) and scope != stop.notice_scope:
        stop.notice_scope = scope
        changes.append("für " + dict(rules.SCOPES)[scope])
    if units is not None and " ".join(units.split())[:300] != stop.notice_units:
        stop.notice_units = " ".join(units.split())[:300]
        changes.append(f"Wohnungen: {stop.notice_units or '–'}")
    if window_from is not None and window_to is not None:
        start, end = rules.parse_time(window_from), rules.parse_time(window_to)
        if (start, end if start else None) != (stop.notice_from, stop.notice_to):
            stop.notice_from, stop.notice_to = start, (end if start else None)
            changes.append(f"Zeitfenster {start:%H:%M}–{end:%H:%M}" if start and end else
                           f"Zeitfenster ab {start:%H:%M}" if start else "Zeitfenster wieder aus dem Plan")
    if changes:
        stop.save(update_fields=["notice_channel", "notice_scope", "notice_units", "notice_from", "notice_to",
                                 "notice_wanted", "updated_at"])
        _record(user, stop, ", ".join(changes))
    return changes


def mark_sent(stop, user, done=True):
    """✓ aufgehängt / Brief verschickt / Mail an HV verschickt … (or take it back)."""
    if done:
        stop.notice_sent_at, stop.notice_sent_by = timezone.now(), user if user and user.is_authenticated else None
    else:
        stop.notice_sent_at, stop.notice_sent_by = None, None
    stop.save(update_fields=["notice_sent_at", "notice_sent_by", "updated_at"])
    what = rules.SENT_LABELS.get(stop.notice_channel or rules.OTHER, "erledigt")
    _record(user, stop, "✓ " + what if done else "↺ Ankündigung wieder offen")


def trip_reported(trip, user, hung):
    """The Aushang-Fahrt was reported in Mein Tag: ✓ hung -> the appointment counts as announced."""
    appointment = appointment_of(trip)
    if appointment is None:
        return None
    if hung:
        if not appointment.notice_channel:
            appointment.notice_channel = rules.AUSHANG
        appointment.notice_sent_at, appointment.notice_sent_by = timezone.now(), user
        appointment.save(update_fields=["notice_channel", "notice_sent_at", "notice_sent_by", "updated_at"])
        _record(user, appointment, f"✓ Aushang aufgehängt von {user}")
    else:
        _record(user, appointment, f"✗ Aushang konnte nicht aufgehängt werden ({user})")
    return appointment


def trip_info(trip):
    """What the person on the Aushang-Fahrt needs to know (Mein Tag, Excel)."""
    appointment = appointment_of(trip)
    if appointment is None:
        return {"appointment": None}
    window, _ = window_of(appointment)
    return {"appointment": appointment, "date": appointment.tour.date,
            "what": "Montage" if appointment.kind == StopKind.INSTALLATION else "Ablesung",
            "time": (f"zwischen {window[0]:%H:%M} und {window[1]:%H:%M} Uhr" if window and window[1]
                     else f"ab {window[0]:%H:%M} Uhr" if window else ""),
            "units": rules.units_text(appointment.notice_scope, appointment.notice_units),
            "channel": rules.CHANNEL_LABELS.get(appointment.notice_channel, "")}


# --- the page "📄 Aushänge & Ankündigungen" --------------------------------------------------------

class Row:
    """One coming appointment and how its tenants are told."""

    def __init__(self, stop, trip, estimates, today, selected):
        self.stop, self.trip = stop, trip
        self.tour = stop.tour
        self.date = stop.tour.date
        self.kind = stop.kind
        target = stop.building or stop.installation_order
        self.ref = f"AZ {stop.building.file_number}" if stop.kind == StopKind.READING else f"RE {stop.installation_order.re_number}"
        self.address = f"{target.street}, {target.zip_code} {target.city}"
        self.manager = stop.building.property_manager if stop.building and stop.building.property_manager_id else None
        self.window, self.window_source = window_of(stop, estimates)
        self.time_text = (f"{self.window[0]:%H:%M}–{self.window[1]:%H:%M} Uhr" if self.window and self.window[1]
                          else f"ab {self.window[0]:%H:%M} Uhr" if self.window else "")
        self.deadline = rules.notice_deadline(self.date)
        self.state, self.label = announce(stop, trip)
        self.late = rules.is_late(self.deadline, today, self.state)
        self.units = rules.units_text(stop.notice_scope, stop.notice_units)
        self.channel_label = rules.CHANNEL_LABELS.get(stop.notice_channel, "")
        self.sent_label = rules.SENT_LABELS.get(stop.notice_channel or rules.OTHER, "erledigt")
        self.trip_hint = rules.trip_hint(trip.tour.date, self.date) if trip else ""
        self.selected = selected
        self.notice = state_of(stop, today, estimates) if stop.notice_printed_at else None

    @property
    def key(self):
        return (self.stop.building_id if self.kind == StopKind.READING else None,
                self.stop.installation_order_id if self.kind == StopKind.INSTALLATION else None)

    @property
    def can_trip(self):
        return self.stop.notice_channel == rules.AUSHANG and self.trip is None and not self.stop.notice_sent_at


def _obj_key(stop):
    if stop.kind == StopKind.INSTALLATION or (stop.kind == StopKind.NOTICE and stop.installation_order_id):
        return ("order", stop.installation_order_id)
    return ("building", stop.building_id)


def announcement_rows(today, horizon=None, kind="", query="", selection=()):
    """Every coming appointment (open readings / installations from today on, within `horizon` days)."""
    stops = (TourStop.objects.filter(kind__in=APPOINTMENT_KINDS, tour__date__gte=today, done_at__isnull=True, outcome="")
             .select_related("tour__employee", "building__property_manager", "installation_order__building",
                             "notice_printed_by", "notice_sent_by")
             .prefetch_related("tour__team").order_by("tour__date", "tour__employee__short_name", "position"))
    if horizon:
        stops = stops.filter(tour__date__lte=today + datetime.timedelta(days=horizon))
    if kind in APPOINTMENT_KINDS:
        stops = stops.filter(kind=kind)
    stops = list(stops)
    words = query.lower().split()
    if words:
        def text(s):
            target = s.building or s.installation_order
            return " ".join([s.building.file_number if s.building else "", getattr(s.installation_order, "re_number", "") or "",
                             target.street, target.zip_code or "", target.city, s.tour.people_label]).lower()
        stops = [s for s in stops if all(w in text(s) for w in words)]
    last = max((s.tour.date for s in stops), default=today)
    trips = {}
    for trip in (TourStop.objects.filter(kind=StopKind.NOTICE, tour__date__lte=last)
                 .select_related("tour__employee").order_by("tour__date")):
        trips.setdefault(_obj_key(trip), []).append(trip)
    estimates, rows, selected = {}, [], set(selection)
    for stop in stops:
        if stop.tour_id not in estimates:
            estimates[stop.tour_id] = estimated_times(stop.tour)
        before = [t for t in trips.get(_obj_key(stop), []) if t.tour.date <= stop.tour.date]
        row = Row(stop, before[-1] if before else None, estimates[stop.tour_id], today, set())
        row.selected = row.key in selected
        rows.append(row)
    return rows


def row_of(stop, today, selection=()):
    row = Row(stop, trip_of(stop), estimated_times(stop.tour), today, set())
    row.selected = row.key in set(selection)
    return row
