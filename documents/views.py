"""
Received cost documents ("Unterlagen") and the 14-day warning.

The warning pop-up is loaded by every page (see base.html) with HTMX:
on page load, every 30 seconds, and right after a change in the table.
It has no close button and ignores Escape. It only disappears when the
building gets a new appointment or its status changes (spec section 7).
"In der Liste ansehen" hides one card for 15 minutes, like the prototype
hides it until the page is opened again.
"""

import datetime

from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from buildings.models import Building
from buildings.services import change_status

from .rules import OVERDUE, RELEASED, SOON
from .services import deadline_entries, set_received_on

HIDE_MINUTES = 15
SESSION_KEY = "deadline_hidden_until"


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
    return render(request, "documents/_warning.html", {
        "shown": overdue[:3], "more": max(0, len(overdue) - 3), "total": len(overdue),
        "counts": _counts(entries),
    })


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
    response["HX-Trigger"] = "deadlines-changed"
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
