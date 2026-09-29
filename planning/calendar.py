"""
Data for the calendar page (FullCalendar reads it as JSON).

One event per tour (person + day, from start to end time) and one
background event per absence. The colour is the person's calendar colour;
badges in the title show the state:
  ⏳ vorläufig   ⟳ neu rechnen   ⚠ Montage-Konflikt
"""

import datetime

from django.db.models import Q
from django.urls import reverse

from conflicts.models import Conflict, Severity

from .models import Absence, StopKind, Tour, TourStatus


def _hours(minutes):
    return f"{minutes / 60:.1f}".replace(".", ",")


def installation_conflicts(tours):
    """Tour ids with an open reading-vs-installation conflict (stored by conflicts/services.py).

    Both tours are marked: the one with the reading and the one with the
    installation. Conflicts accepted knowingly ("bewusst übernommen") are not.
    """
    tour_ids = [tour.pk for tour in tours]
    open_conflicts = Conflict.objects.filter(
        acknowledged_at__isnull=True, severity__in=[Severity.CRITICAL, Severity.WARNING]
    ).filter(Q(stop__tour_id__in=tour_ids) | Q(other_stop__tour_id__in=tour_ids))
    clashing = set()
    for stop_tour, other_tour in open_conflicts.values_list("stop__tour_id", "other_stop__tour_id"):
        clashing.update(t for t in (stop_tour, other_tour) if t in tour_ids)
    return clashing


KIND_ICONS = {"reading": "📖", "installation": "🔧", "mixed": "📖🔧"}


def tour_kind(stops):
    """'reading', 'installation' or 'mixed' (both in one plan)."""
    kinds = {s.kind for s in stops}
    if kinds == {StopKind.INSTALLATION}:
        return "installation"
    return "mixed" if StopKind.INSTALLATION in kinds else "reading"


def _tooltip(tour, stops):
    """Text shown when the mouse is over a tour: one line per stop."""
    lines = [f"{tour.people_label} · {tour.date:%d.%m.%Y} · {tour.get_status_display()} · netto {_hours(tour.net_minutes)} h"]
    if len(tour.people) > 1:
        lines.append(f"👥 Team: {tour.people_label}" + (" – Arbeitszeit aufgeteilt" if tour.split_work else ""))
    if tour.time_state:
        lines.append("⏱ mehr als 7,5 h" if tour.time_state == "over" else "⏱ weniger als 6 h")
    for stop in stops:
        target = stop.building or stop.installation_order
        icon = "🔧" if stop.kind == StopKind.INSTALLATION else "📖"
        when = f"{stop.start_time:%H:%M} " if stop.start_time else ""
        lines.append(f"{when}{icon} {target.street}, {target.city}" if target else f"{when}{icon}")
    return "\n".join(lines)


def calendar_events(start, end, employees, editable, kind=""):
    """kind: '' = all plans, or only 'reading' / 'installation' / 'mixed' plans."""
    tours = list(
        # plans led by these people, and plans where they are in the team
        Tour.objects.filter(Q(employee__in=employees) | Q(team__in=employees), date__gte=start, date__lt=end).distinct()
        .select_related("employee").prefetch_related("stops__building", "stops__installation_order", "team")
    )
    clashing = installation_conflicts(tours)
    events = []
    for tour in tours:
        stops = sorted(tour.stops.all(), key=lambda s: s.position)
        plan_kind = tour_kind(stops)
        if kind and plan_kind != kind:
            continue
        badges = []
        if tour.status == TourStatus.PROVISIONAL:
            badges.append("⏳")
        if tour.needs_recalculation:
            badges.append("⟳")
        if tour.pk in clashing:
            badges.append("⚠")
        if tour.time_state:
            badges.append("⏱")  # info: more than 7,5 h / less than 6 h
        installations = sum(1 for s in stops if s.kind == StopKind.INSTALLATION)
        readings = len(stops) - installations
        what = " · ".join(([f"{readings}× Ablesung"] if readings else []) + ([f"{installations}× Montage"] if installations else []))
        end_time = tour.end_time or (datetime.datetime.combine(tour.date, tour.start_time)
                                     + datetime.timedelta(minutes=tour.work_minutes + tour.drive_minutes)).time()
        events.append({
            "id": tour.pk,
            "title": " ".join(badges + [f"{KIND_ICONS[plan_kind]} {'👥 ' if len(tour.people) > 1 else ''}{tour.people_label} · {what} · {_hours(tour.work_minutes + tour.drive_minutes)} h"]),
            "start": datetime.datetime.combine(tour.date, tour.start_time).isoformat(),
            "end": datetime.datetime.combine(tour.date, end_time).isoformat(),
            "backgroundColor": tour.employee.calendar_color,
            "borderColor": "#d03b3b" if tour.pk in clashing else ("#fab219" if tour.needs_recalculation else tour.employee.calendar_color),
            "classNames": ["tour", tour.status, f"kind-{plan_kind}"] + (["needs-recalc"] if tour.needs_recalculation else []),
            "editable": editable,
            "extendedProps": {"version": tour.version, "detailUrl": reverse("planning:tour_detail", args=[tour.pk]),
                              "kind": plan_kind, "tooltip": _tooltip(tour, stops)},
        })
    for absence in Absence.objects.filter(employee__in=employees, start_date__lt=end, end_date__gte=start).select_related("employee"):
        events.append({
            "title": f"{absence.employee}: {absence.get_kind_display()}",
            "start": absence.start_date.isoformat(),
            "end": (absence.end_date + datetime.timedelta(days=1)).isoformat(),  # FullCalendar: end is exclusive
            "allDay": True,
            "display": "background",
            "backgroundColor": absence.employee.calendar_color,
            "classNames": ["absence"],
        })
        events.append({  # visible label in the month view
            "title": f"🏖 {absence.employee}: {absence.get_kind_display()}",
            "start": absence.start_date.isoformat(),
            "end": (absence.end_date + datetime.timedelta(days=1)).isoformat(),
            "allDay": True, "editable": False, "classNames": ["absence-label"],
            "backgroundColor": "transparent", "borderColor": absence.employee.calendar_color, "textColor": "#52514e",
        })
    return events
