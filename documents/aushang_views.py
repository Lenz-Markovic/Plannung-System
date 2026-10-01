"""📄 Aushänge per Fahrplan (Terminierung: what still has to be printed) and the 🗺 Aushang-Route."""

import datetime
from functools import wraps

from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from planning.models import Employee, StopKind, TourStop

from . import notice_overview as overview
from . import notice_rules as rules
from . import notices

VIEW = "planning.view_tour"
HORIZONS = [("14", "nächste 2 Wochen"), ("28", "nächste 4 Wochen"), ("56", "nächste 8 Wochen"), ("alle", "alle geplanten")]


def may_edit(user):
    """Disposition / Terminierung (change plans) and Sachbearbeitung (works through the Rückmeldungen)."""
    return user.has_perm("planning.change_tour") or user.has_perm("planning.process_visit")


def edit_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not may_edit(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


@permission_required(VIEW, raise_exception=True)
def aushaenge_page(request):
    from buildings.views import clean_url

    today = timezone.localdate()
    horizon = request.GET.get("zeitraum", "28") if request.GET.get("zeitraum", "28") in dict(HORIZONS) else "28"
    only_open = request.GET.get("f", "offen") != "alle"
    query = request.GET.get("q", "").strip()
    found = overview.blocks(today, None if horizon == "alle" else int(horizon), only_open, query)
    context = {"blocks": found, "horizons": HORIZONS, "horizon": horizon, "only_open": only_open, "q": query,
               "today": today, "scopes": rules.SCOPES, "may_edit": may_edit(request.user),
               "missing": sum(len(b.missing) for b in found)}
    if request.htmx_target == "aushang-blocks":
        response = render(request, "documents/_aushaenge_blocks.html", context)
        response["HX-Push-Url"] = clean_url(request)
        return response
    return render(request, "documents/aushaenge.html", context)


def _stop(pk):
    stop = get_object_or_404(TourStop.objects.select_related("tour__employee", "building", "installation_order",
                                                             "notice_printed_by"), pk=pk)
    if stop.kind == StopKind.HELP:
        raise PermissionDenied
    return stop


def _row(request, stop, message="", error=False):
    today = timezone.localdate()
    html = render_to_string("documents/_aushang_row.html", {
        "s": overview.notice_stop(stop, today), "scopes": rules.SCOPES, "may_edit": True}, request=request)
    block = overview.block_of(stop.tour, today)
    if block is not None:  # the counts in the head of the Fahrplan change, too
        html += render_to_string("documents/_aushang_head.html", {"b": block, "today": today, "oob": True}, request=request)
    if message:
        html += render_to_string("core/_toast.html", {"message": message, "error": error}, request=request)
    return HttpResponse(html)


@require_POST
@edit_required
def aushang_wanted(request, pk):
    """＋ Aushang / ✕ kein Aushang for one appointment."""
    stop = _stop(pk)
    on = request.POST.get("on") == "1"
    notices.set_wanted(stop, on)
    from journal.activity import day_label, record
    from journal.models import ActivityKind
    target = stop.building or stop.installation_order
    record(request.user, ActivityKind.NOTICE, f"Aushang {'ja' if on else 'nein'}: {target.street} "
           f"({stop.tour.employee} {day_label(stop.tour.date)})", tour=stop.tour, building=stop.building,
           order=stop.installation_order)
    return _row(request, stop, "Aushang nötig" if on else "Kein Aushang")


@require_POST
@edit_required
def aushang_edit(request, pk):
    """✎ Aushang ans Haus / Briefe an einzelne Wohnungen, and the time by hand."""
    stop = _stop(pk)
    for name in ("von", "bis"):
        if request.POST.get(name, "").strip() and rules.parse_time(request.POST.get(name)) is None:
            response = _row(request, stop, "Zeit bitte als 8:00 oder 08:30 eingeben.", error=True)
            response["HX-Reswap"] = "none"
            return response
    changes = notices.set_notice(stop, request.user, scope=request.POST.get("scope"), units=request.POST.get("units"),
                                 window_from=request.POST.get("von", ""), window_to=request.POST.get("bis", ""))
    return _row(request, stop, "Gespeichert: " + ", ".join(changes) if changes else "Nichts geändert")


@permission_required(VIEW, raise_exception=True)
def aushang_route(request):
    """🗺 The route for hanging the ticked Aushänge / Briefe: order, times, map - to print (or Excel)."""
    from planning.tomtom import current_api_key, get_client

    ids = [int(v) for v in request.GET.getlist("stop") if v.isdigit()]
    today = timezone.localdate()
    try:
        day = datetime.date.fromisoformat(request.GET.get("datum", "")) if request.GET.get("datum") else today
    except ValueError:
        day = today
    start = rules.parse_time(request.GET.get("ab", "")) or datetime.time(8, 0)
    person = Employee.objects.filter(pk=request.GET.get("person")).first() if request.GET.get("person", "").isdigit() else None
    from_home = request.GET.get("start") == "zuhause" and person is not None and person.street
    client = get_client()
    start_point = None
    if from_home:
        from planning import geocoding
        start_point = geocoding.position(person, client)[0]
    found = overview.route(ids, day, start, start_point, client)
    context = {
        "route": found, "ids": ids, "day": day, "start": start, "person": person, "from_home": bool(from_home),
        "people": Employee.objects.filter(active=True).order_by("short_name"), "today": today,
        "total_km": sum((s.drive_km or 0) for s in found), "total_drive": sum(s.drive_minutes for s in found),
        "total_work": sum(s.minutes for s in found), "papers": sum(len(s.papers) for s in found),
        "end": found[-1].leave if found else 0, "all_tomtom": bool(found) and all(s.drive_from_tomtom for s in found[1:]),
        "map_data": overview.route_map_data(found, start_point), "map_available": bool(found) and bool(current_api_key()),
    }
    context["end_text"] = f"{context['end'] // 60:02d}:{context['end'] % 60:02d}"
    if request.GET.get("format") == "xlsx" and found:
        from .aushang_route_excel import route_workbook
        response = HttpResponse(route_workbook(context), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = f'attachment; filename="Aushang_Route_{day:%Y-%m-%d}.xlsx"'
        return response
    return render(request, "documents/aushang_route.html", context)
