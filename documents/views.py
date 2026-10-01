"""
Received cost documents ("Unterlagen") and the 14-day warning.

The warning pop-up is loaded by every page (see base.html) with HTMX:
on page load, every 30 seconds, and right after a change in the table.

It cannot be switched off (spec section 7): the problem is only solved
when the building gets a new appointment or its status changes. But the
user can put it aside with ✕ / "Später erinnern" so they can keep working;
it comes back automatically (15 min, 1 hour or tomorrow morning) and a
small reminder bar stays visible meanwhile. "In der Liste ansehen" hides
one card for 15 minutes.
"""

import datetime

from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from buildings.models import Building
from buildings.services import change_status
from planning.models import StopKind, TourStop

from journal.activity import day_label, record, streets

from .notice_rules import notice_deadline
from journal.models import ActivityKind

from . import notices
from .aushang_docx import DOCX_TYPE, build_docx
from .aushang_fields import BOX_LABELS, WEEKDAYS
from .rules import OVERDUE, RELEASED, SNOOZE_CHOICES, SOON, remind_again_at
from .services import deadline_entries, set_received_on

HIDE_MINUTES = 15
SESSION_KEY = "deadline_hidden_until"
SNOOZE_KEY = "deadline_snoozed_until"


def _snoozed_until(request):
    """Datetime until which the whole pop-up is put aside, or None."""
    value = request.session.get(SNOOZE_KEY)
    if not value:
        return None
    until = datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc)
    if until <= timezone.now():
        del request.session[SNOOZE_KEY]
        return None
    return timezone.localtime(until)


def sees_deadline_warning(user):
    """The warning is for the roles that can act on it (plan or change the status)."""
    return user.has_perm("buildings.set_status_open_rework") or user.has_perm("buildings.release_building")


def _hidden_ids(request):
    now = timezone.now().timestamp()
    hidden = {pk: until for pk, until in request.session.get(SESSION_KEY, {}).items() if until > now}
    request.session[SESSION_KEY] = hidden
    return {int(pk) for pk in hidden}


def _counts(entries):
    open_entries = [e for e in entries if e.info.state != RELEASED]
    return {
        "open": len(open_entries),
        "overdue": sum(1 for e in open_entries if e.info.state == OVERDUE),
        "soon": sum(1 for e in open_entries if e.info.state == SOON),
        "released": len(entries) - len(open_entries),
        "all": len(entries),
    }


@login_required
def deadline_warning(request):
    if not sees_deadline_warning(request.user):
        return HttpResponse("")
    entries = deadline_entries()
    hidden = _hidden_ids(request)
    overdue = sorted(
        (e for e in entries if e.info.state == OVERDUE and e.building.pk not in hidden),
        key=lambda e: (e.info.days_left, e.building.file_number),
    )
    snoozed_until = _snoozed_until(request)
    return render(request, "documents/_warning.html", {
        "shown": [] if snoozed_until else overdue[:3],
        "more": max(0, len(overdue) - 3), "total": len(overdue),
        "counts": _counts(entries), "snoozed_until": snoozed_until if overdue else None,
        "snooze_choices": SNOOZE_CHOICES,
    })


@require_POST
@login_required
def warning_snooze(request):
    """✕ / 'Später erinnern': put the pop-up aside; 'jetzt' shows it again at once."""
    choice = request.POST.get("until", "15")
    if choice == "jetzt":
        request.session.pop(SNOOZE_KEY, None)
    else:
        request.session[SNOOZE_KEY] = remind_again_at(choice, timezone.localtime()).timestamp()
    return deadline_warning(request)


@require_POST
@login_required
def warning_status(request, pk):
    """Status button in the pop-up: change the status, then show the pop-up again."""
    building = get_object_or_404(Building, pk=pk)
    try:
        change_status(building, request.POST.get("status", ""), request.user)
    except ValidationError as error:
        return HttpResponseBadRequest(str(error))
    response = deadline_warning(request)
    response.write(render(request, "core/_toast.html", {"message": f"Status gespeichert · {building.file_number}"}).content)
    response["HX-Trigger"] = "buildings-changed"
    return response


@require_POST
@login_required
def warning_show_in_list(request, pk):
    """Hide this card for a while and open the building in the list."""
    building = get_object_or_404(Building, pk=pk)
    hidden = request.session.get(SESSION_KEY, {})
    hidden[str(pk)] = (timezone.now() + datetime.timedelta(minutes=HIDE_MINUTES)).timestamp()
    request.session[SESSION_KEY] = hidden
    response = HttpResponse("")
    response["HX-Redirect"] = f"{reverse('buildings:list')}?q={building.file_number}"
    return response


FILTERS = {
    "offen": lambda e: e.info.state != RELEASED,
    "ueber": lambda e: e.info.state == OVERDUE,
    "bald": lambda e: e.info.state == SOON,
    "frei": lambda e: e.info.state == RELEASED,
    "alle": lambda e: True,
}
STATE_ORDER = {OVERDUE: 0, SOON: 1, "ok": 2, RELEASED: 3}
SORTS = {
    "frist": lambda e: (STATE_ORDER[e.info.state], e.info.days_left, e.building.file_number),
    "eingang": lambda e: (e.receipt.received_on, e.building.file_number),
    "hv": lambda e: ((e.building.property_manager.name if e.building.property_manager else "~").lower(), e.info.days_left),
    "ort": lambda e: (e.building.city, e.building.street),
}


@permission_required("documents.view_costdocumentreceipt", raise_exception=True)
def receipt_list(request):
    """'Unterlagen erhalten' list with deadlines (ulRender() in the prototype)."""
    entries = deadline_entries()
    chosen = request.GET.get("f", "offen")
    sort = request.GET.get("sort", "frist")
    query = request.GET.get("q", "").strip().lower()
    shown = [e for e in entries if FILTERS.get(chosen, FILTERS["offen"])(e)]
    if query:
        shown = [e for e in shown if query in " ".join([
            e.building.file_number, e.building.street, e.building.city, e.building.zip_code,
            e.building.property_manager.name if e.building.property_manager else "", e.planned_reader or "",
        ]).lower()]
    shown.sort(key=SORTS.get(sort, SORTS["frist"]))
    context = {"entries": shown, "counts": _counts(entries), "chosen": chosen, "sort": sort}
    template = "documents/_receipt_entries.html" if request.htmx_target == "receipt-entries" else "documents/receipt_list.html"
    return render(request, template, context)


@require_POST
@permission_required("documents.view_costdocumentreceipt", raise_exception=True)
def receipt_update(request, pk):
    """Change status or received date directly in the list; returns the updated card."""
    building = get_object_or_404(Building, pk=pk)
    try:
        if "status" in request.POST:
            change_status(building, request.POST["status"], request.user)
        elif "received_on" in request.POST:
            if not request.user.has_perm("documents.change_costdocumentreceipt"):
                raise PermissionDenied("Den Unterlagen-Eingang darf deine Rolle nicht ändern.")
            value = request.POST["received_on"].strip()
            set_received_on(building, datetime.date.fromisoformat(value) if value else None, request.user)
    except (ValidationError, ValueError) as error:
        return HttpResponseBadRequest(str(error))
    entry = next((e for e in deadline_entries() if e.building.pk == pk), None)
    response = render(request, "documents/_receipt_entry.html", {"e": entry, "removed_pk": pk})
    response["HX-Trigger"] = "deadlines-changed"
    return response


# --- Tenant notices (Aushang) -------------------------------------------------------------

STALE = "Der Plan wurde inzwischen geändert – bitte den Kalender neu laden und nochmal drucken."


def _stale():
    """The page had stop ids that do not exist any more (every save of a plan makes new stops)."""
    response = HttpResponse(f"<p style='font-family:sans-serif;padding:20px'>⚠ {STALE}</p>", status=409)
    return response


def _chosen_stops(values):
    values = [v for v in values if str(v).isdigit()]  # "?stop=abc" is ignored, not a 500
    return list(TourStop.objects.filter(pk__in=values).select_related(
        "tour__employee", "building", "installation_order").prefetch_related("installation_order__items__category")
        .order_by("tour__date", "tour__employee__short_name", "position"))


def _notice_pages(stops):
    """What every page shows: the fields of the company template."""
    pages, estimates = [], {}
    for stop in notices.notice_stops(stops):
        if stop.tour_id not in estimates:
            estimates[stop.tour_id] = notices.estimated_times(stop.tour)
        state = notices.state_of(stop, estimates=estimates[stop.tour_id])
        for flat, fields in notices.pages_of(stop, estimates[stop.tour_id]):  # one Aushang, or one Brief per flat
            pages.append({"stop": stop, "fields": fields, "state": state, "flat": flat})
    return pages


@require_POST
@permission_required("planning.change_tour", raise_exception=True)
def notice_toggle(request):
    """📄 Aushang ja / nein for one or more stops (optional - not every building gets one)."""
    stops = [s for s in _chosen_stops(request.POST.getlist("stop")) if s.kind != StopKind.HELP]
    if not stops:
        return HttpResponseBadRequest("Kein Stopp gewählt.")
    on = request.POST.get("on") == "1"
    for stop in stops:
        notices.set_wanted(stop, on)
    tour = stops[0].tour
    record(request.user, ActivityKind.NOTICE, f"Aushang {'ja' if on else 'nein'} ({len(stops)}): {streets(stops)} · "
           f"Plan {tour.employee} {day_label(tour.date)}", tour=tour)
    from planning.views import tour_detail  # the side panel of the plan, shown again
    return tour_detail(request, stops[0].tour_id)


@require_POST
@permission_required("planning.change_tour", raise_exception=True)
def notice_print(request):
    """📄 Aushänge drucken / als Word: mark the chosen stops as printed, then the page or the .docx."""
    stops = [s for s in _chosen_stops(request.POST.getlist("stop")) if s.kind != StopKind.HELP]
    if not stops:
        return _stale()
    notices.mark_printed(stops, request.user)
    for tour in {s.tour for s in stops}:  # one line per plan
        mine = [s for s in stops if s.tour == tour]
        record(request.user, ActivityKind.NOTICE,
               f"Aushang {'als Word geladen' if request.POST.get('format') == 'docx' else 'gedruckt'} ({len(mine)}): "
               f"{streets(mine)} · Plan {tour.employee} {day_label(tour.date)}", tour=tour)
    if request.POST.get("format") == "docx":
        return _docx_response(stops)
    return redirect(f"{reverse('documents:notice_page')}?{'&'.join(f'stop={s.pk}' for s in stops)}")


def _docx_response(stops):
    data = build_docx([page["fields"] for page in _notice_pages(stops)])
    first = stops[0] if stops else None
    name = f"Aushang_{first.tour.date:%Y-%m-%d}_{first.tour.employee}.docx" if first else "Aushang.docx"
    response = HttpResponse(data, content_type=DOCX_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@permission_required("planning.change_tour", raise_exception=True)
def notice_confirm(request):
    """Too late (less than 14 days before) or the day is over: ask first, then print anyway."""
    stops = [s for s in _chosen_stops(request.GET.getlist("stop")) if s.kind != StopKind.HELP]
    if not stops:
        response = render(request, "core/_toast.html", {"message": STALE, "error": True})
        response["HX-Reswap"] = "none"
        return response
    today = timezone.localdate()
    days = []
    for tour in sorted({s.tour for s in stops}, key=lambda t: t.date):
        mine = [s for s in stops if s.tour == tour]
        days.append({"date": tour.date, "left": (tour.date - today).days, "past": tour.date < today,
                     "deadline": notice_deadline(tour.date), "streets": streets(mine, limit=6)})
    return render(request, "documents/_notice_confirm.html", {
        "stops": stops, "days": days, "docx": request.GET.get("format") == "docx"})


@permission_required("planning.view_tour", raise_exception=True)
def notice_page(request):
    """The notices on the company template, one A4 page each - print or save as PDF with the browser."""
    stops = _chosen_stops(request.GET.getlist("stop"))
    if request.GET.get("format") == "docx":
        if not stops:
            return _stale()
        return _docx_response(stops)
    return render(request, "documents/aushang.html", {
        "pages": _notice_pages(stops), "stop_ids": [s.pk for s in stops],
        "weekdays": WEEKDAYS, "labels": BOX_LABELS,
    })
