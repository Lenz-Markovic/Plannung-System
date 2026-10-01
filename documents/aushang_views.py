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
FILTERS = [("offen", "🖨 noch zu drucken"), ("entscheiden", "❓ noch entscheiden"),
           ("bereit", "🚗 gedruckt – bereit zum Verteilen"), ("alle", "alle Fahrpläne")]
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
    chosen = request.GET.get("f", "offen") if request.GET.get("f", "offen") in dict(FILTERS) else "offen"
    query = request.GET.get("q", "").strip()
    found = overview.blocks(today, None if horizon == "alle" else int(horizon), chosen == "offen", query,
                            ready=chosen == "bereit", undecided=chosen == "entscheiden")
    context = {"blocks": found, "horizons": HORIZONS, "horizon": horizon, "only_open": chosen == "offen", "q": query,
               "filters": FILTERS, "chosen": chosen, "ready": sum(len(b.printed) for b in found),
               "areas": overview.page_areas(found),
               "today": today, "scopes": rules.SCOPES, "may_edit": may_edit(request.user),
               "missing": sum(len(b.missing) for b in found), "undecided": sum(len(b.undecided) for b in found),
               "choices": rules.CHOICES, "choice_titles": rules.CHOICE_TITLES, "access_scopes": rules.ACCESS_SCOPES}
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
        "s": overview.notice_stop(stop, today), "scopes": rules.SCOPES, "may_edit": True, "choices": rules.CHOICES,
        "choice_titles": rules.CHOICE_TITLES, "access_scopes": rules.ACCESS_SCOPES}, request=request)
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


def _refused(request, stop, error):
    response = _row(request, stop, " ".join(error.messages), error=True)
    response["HX-Reswap"] = "none"   # the row stays as it was, only the reason shows
    return response


@require_POST
@edit_required
def aushang_choice(request, pk):
    """Ankündigung: 📄 Aushang / ✉ Briefe / ☎ telefonisch / 📧 per Mail / – keine (the Terminierung decides)."""
    from django.core.exceptions import ValidationError

    stop = _stop(pk)
    try:
        notices.set_choice(stop, request.user, request.POST.get("choice", ""), request.POST.get("units"))
    except ValidationError as error:
        return _refused(request, stop, error)
    return _row(request, stop, f"Ankündigung: {dict(rules.CHOICES)[stop.notice_choice]}")


@require_POST
@edit_required
def aushang_access(request, pk):
    """Zugang: in alle Wohnungen / nur in diese Wohnungen / nicht in die Wohnungen."""
    from django.core.exceptions import ValidationError

    stop = _stop(pk)
    try:
        notices.set_access(stop, request.user, request.POST.get("access", ""), request.POST.get("access_units", ""))
    except ValidationError as error:
        return _refused(request, stop, error)
    return _row(request, stop, f"Zugang: {stop.access_text}")


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
    """🗺 The round trip office -> houses -> office for hanging the ticked Aushänge / Briefe: order,
    drive and minutes, map - to print (or Excel). Day and start time are optional (the drivers are flexible)."""
    from planning.tomtom import current_api_key, get_client

    ids = [int(v) for v in request.GET.getlist("stop") if v.isdigit()]
    try:
        day = datetime.date.fromisoformat(request.GET["datum"]) if request.GET.get("datum") else None
    except ValueError:
        day = None
    start = rules.parse_time(request.GET.get("ab", ""))
    person = Employee.objects.filter(pk=request.GET.get("person")).first() if request.GET.get("person", "").isdigit() else None
    found, back, base = overview.route(ids, day, start, get_client())
    work = sum(s.minutes for s in found)
    drive = sum(s.drive_minutes for s in found) + back.minutes
    context = {
        "route": found, "back": back, "office": base, "ids": ids, "day": day, "start": start, "person": person,
        "people": Employee.objects.filter(active=True).order_by("short_name"), "has_clock": start is not None,
        "total_km": sum((s.drive_km or 0) for s in found) + (back.km or 0), "total_drive": drive, "total_work": work,
        "total": rules.duration_text(drive + work), "papers": sum(len(s.papers) for s in found),
        "end": back.arrive, "all_tomtom": bool(found) and all(s.drive_from_tomtom for s in found) and back.from_tomtom,
        "map_data": overview.route_map_data(found, back, base), "map_available": bool(found) and bool(current_api_key()),
        "areas": overview.route_areas(found), "far": [s for s in found if s.far_km],
        "keep": {k: v for k, v in request.GET.items() if k in ("person", "datum", "ab")},
    }
    context["end_text"] = f"{back.arrive // 60:02d}:{back.arrive % 60:02d}"
    if request.GET.get("format") == "xlsx" and found:
        from .aushang_route_excel import route_workbook
        response = HttpResponse(route_workbook(context), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        name = f"Aushang_Route_{day:%Y-%m-%d}.xlsx" if day else "Aushang_Route.xlsx"
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response
    return render(request, "documents/aushang_route.html", context)
