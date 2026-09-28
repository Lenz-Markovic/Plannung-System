"""
Data for the calendar page (FullCalendar reads it as JSON).

One event per tour (person + day, from start to end time) and one
background event per absence. The colour is the person's calendar colour;
badges in the title show the state:
  ⏳ vorläufig   ⟳ neu rechnen   ⚠ Montage-Konflikt
"""

import datetime

from django.urls import reverse

from conflicts.rules import CRITICAL, WARNING, check_installation_vs_reading

from .models import Absence, StopKind, Tour, TourStatus, TourStop


def _hours(minutes):
    return f"{minutes / 60:.1f}".replace(".", ",")


def installation_conflicts(tours):
    """Tour ids with a reading that clashes with an installation (spec section 8)."""
    reading = {}
    for tour in tours:
        for stop in tour.stops.all():
            if stop.kind == StopKind.READING:
                reading.setdefault(stop.building_id, []).append(tour)
    installations = (TourStop.objects.filter(kind=StopKind.INSTALLATION, installation_order__building_id__in=reading)
                     .select_related("tour", "installation_order"))
    clashing = set()
    for stop in installations:
        for tour in reading.get(stop.installation_order.building_id, []):
            if check_installation_vs_reading(tour.date, stop.tour.date).severity in (CRITICAL, WARNING):
                clashing.add(tour.pk)
    return clashing


def calendar_events(start, end, employees, editable):
    tours = list(
        Tour.objects.filter(date__gte=start, date__lt=end, employee__in=employees)
        .select_related("employee").prefetch_related("stops")
    )
    clashing = installation_conflicts(tours)
    events = []
    for tour in tours:
        stops = list(tour.stops.all())
        badges = []
        if tour.status == TourStatus.PROVISIONAL:
            badges.append("⏳")
        if tour.needs_recalculation:
            badges.append("⟳")
        if tour.pk in clashing:
            badges.append("⚠")
        installations = sum(1 for s in stops if s.kind == StopKind.INSTALLATION)
        what = f"{len(stops)} Stopp{'s' if len(stops) != 1 else ''}"
        if installations:
            what += f" · {installations}× Montage"
        end_time = tour.end_time or (datetime.datetime.combine(tour.date, tour.start_time)
                                     + datetime.timedelta(minutes=tour.work_minutes + tour.drive_minutes)).time()
        events.append({
            "id": tour.pk,
            "title": " ".join(badges + [f"{tour.employee} · {what} · {_hours(tour.work_minutes + tour.drive_minutes)} h"]),
            "start": datetime.datetime.combine(tour.date, tour.start_time).isoformat(),
            "end": datetime.datetime.combine(tour.date, end_time).isoformat(),
            "backgroundColor": tour.employee.calendar_color,
            "borderColor": "#d03b3b" if tour.pk in clashing else ("#fab219" if tour.needs_recalculation else tour.employee.calendar_color),
            "classNames": ["tour", tour.status] + (["needs-recalc"] if tour.needs_recalculation else []),
            "editable": editable,
            "extendedProps": {"version": tour.version, "detailUrl": reverse("planning:tour_detail", args=[tour.pk])},
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
