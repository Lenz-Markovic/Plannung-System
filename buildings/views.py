"""
Building list ("Liegenschaften-Dashboard").

How HTMX is used here (no own JavaScript needed):
- The filter form sends every change with hx-get to building_list.
  For an HTMX request we only return the part below the filters
  (_results.html); the browser swaps it in and updates the URL.
- The last table row "weitere laden" fetches the next page (building_rows)
  as soon as it becomes visible - like the endless scrolling in the prototype.
- The ▸ button in a row fetches the same row plus a detail row (building_row).
"""

import datetime

from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from documents.models import CostDocumentReceipt
from documents.rules import deadline_info
from documents.services import set_received_on
from planning.models import TourStop
from planning.services import plan_bar_context

from . import services
from .display import building_schedule
from .filters import BuildingFilter, sort_buildings
from .models import Building, BuildingStatus, PropertyManager
from .selectors import building_list_queryset, building_summary, source_summary, with_schedule_details

PAGE_SIZE = 100


def filtered_buildings(request):
    """Apply the filters and the sorting from the URL. Used by all list views."""
    building_filter = BuildingFilter(request.GET, queryset=building_list_queryset())
    buildings = sort_buildings(building_filter.qs, request.GET.get("sort", ""))
    return building_filter, buildings


def clean_url(request):
    params = request.GET.copy()
    for key in [key for key, value in params.items() if value == ""]:
        del params[key]
    return f"{request.path}?{params.urlencode()}" if params else request.path


def prepare_row(building, today):
    """Attach what the row template needs: schedule column and deadline."""
    building.schedule = building_schedule(building)
    # A missing one-to-one raises an AttributeError subclass, so getattr's default works.
    receipt = getattr(building, "cost_documents", None)
    building.deadline = (
        deadline_info(receipt.deadline_start, building.status == BuildingStatus.RELEASED, today) if receipt else None
    )
    return building


def page_rows(buildings, page_number):
    """One page of rows, with the schedule column prepared for each row."""
    page = Paginator(buildings, PAGE_SIZE).get_page(page_number)
    today = timezone.localdate()
    rows = [prepare_row(building, today) for building in with_schedule_details(page.object_list)]
    return page, rows


@permission_required("buildings.view_building", raise_exception=True)
def building_list(request):
    building_filter, buildings = filtered_buildings(request)
    if request.htmx_target == "kpis":
        # Only the tiles, after a change in a row (see _kpis.html).
        return render(request, "buildings/_kpis.html", {"summary": building_summary(building_filter.qs)})
    page, rows = page_rows(buildings, 1)
    context = {
        "filter": building_filter,
        "page": page,
        "rows": rows,
        "summary": building_summary(building_filter.qs),
        "sources": source_summary(),
        "property_managers": PropertyManager.objects.values_list("name", flat=True),
        **plan_bar_context(request.session),
    }
    if request.htmx_target == "results":
        response = render(request, "buildings/_results.html", context)
        # Show only the filters that are really set in the address bar
        # (?region=Region+Calw instead of ?q=&stichtag=&region=Region+Calw&...).
        response["HX-Push-Url"] = clean_url(request)
        return response
    context["live_since"] = timezone.now().timestamp()
    return render(request, "buildings/list.html", context)


@permission_required("buildings.view_building", raise_exception=True)
def building_rows(request):
    """Next page of rows for the 'weitere laden' row."""
    _, buildings = filtered_buildings(request)
    page, rows = page_rows(buildings, request.GET.get("page", 2))
    return render(request, "buildings/_rows.html", {"page": page, "rows": rows})


@permission_required("buildings.view_building", raise_exception=True)
def building_row(request, pk):
    """One row, opened (?open=1: with detail row) or closed again."""
    return render_row(request, pk, opened=request.GET.get("open") == "1")


def render_row(request, pk, opened=False, message="", error=False):
    building = get_object_or_404(with_schedule_details(building_list_queryset()), pk=pk)
    prepare_row(building, timezone.localdate())
    return render(request, "buildings/_row_toggle.html", {"b": building, "open": opened, "message": message,
                                                          "error": error})


SAVED_MESSAGES = {
    "status": "Status gespeichert",
    "note": "Notiz gespeichert",
    "property_manager": "Hausverwaltung gespeichert",
    "received_on": "Unterlagen-Eingang gespeichert",
    "accept_proposal": "Vorschlag übernommen",
}


@require_POST
@permission_required("buildings.view_building", raise_exception=True)
def building_update(request, pk):
    """Save one field edited directly in the table and return the new row.

    The form elements in the row send exactly one of these values:
    status, note, property_manager, received_on.
    """
    building = get_object_or_404(Building, pk=pk)
    data = request.POST
    try:
        if "status" in data:
            services.change_status(building, data["status"], request.user)
        elif "note" in data:
            services.set_note(building, data["note"], request.user)
        elif "property_manager" in data:
            services.set_property_manager(building, data["property_manager"], request.user)
        elif "accept_proposal" in data:
            services.accept_proposal(building, request.user)
        elif "received_on" in data:
            if not request.user.has_perm("documents.change_costdocumentreceipt"):
                raise PermissionDenied("Den Unterlagen-Eingang darf deine Rolle nicht eintragen.")
            value = data["received_on"].strip()
            set_received_on(building, datetime.date.fromisoformat(value) if value else None, request.user)
        else:
            return HttpResponseBadRequest("Kein Feld angegeben.")
    except ValidationError as error:
        # the row again with what is really saved (the dropdown jumps back) and the reason
        return render_row(request, pk, message=" ".join(error.messages), error=True)
    except ValueError as error:
        return HttpResponseBadRequest(str(error))

    field = next(key for key in SAVED_MESSAGES if key in data)
    response = render_row(request, pk, message=f"{SAVED_MESSAGES[field]} · {building.file_number}")
    # Tells the warning pop-up (base.html) to check the deadlines again and
    # the KPI tiles to reload their numbers.
    response["HX-Trigger"] = "deadlines-changed, buildings-changed"
    return response


LIVE_MAX_ROWS = 50  # more changes at once: only the toast, the user can reload


def changed_by_others(since, user):
    """{building pk: {names of the people}} changed after `since` by someone else.

    Uses the change history (django-simple-history), so we also know WHO changed it:
    buildings, cost document receipts and stops of tours (e.g. a reader ticks "erledigt").
    """
    changes = {}
    sources = [
        (Building.history, "id"),
        (CostDocumentReceipt.history, "building_id"),
        (TourStop.history, "building_id"),
    ]
    for history, field in sources:
        rows = (history.filter(history_date__gt=since).exclude(history_user=user)
                .exclude(**{f"{field}__isnull": True}).values_list(field, "history_user__username"))
        for pk, name in rows:
            changes.setdefault(pk, set()).add(name or "System")
    return changes


@permission_required("buildings.view_building", raise_exception=True)
def building_changes(request):
    """Near-live list: polled every 20 s by the #live element in list.html.

    Answers with a new #live element (next check starts from "now"), the changed
    rows as "out of band" swaps (rows not on the page are simply ignored) and a toast.
    """
    now = timezone.now()
    try:
        since = datetime.datetime.fromtimestamp(float(request.GET.get("seit", "")), tz=datetime.timezone.utc)
    except ValueError:
        since = now
    changes = changed_by_others(since, request.user)
    rows = []
    if changes and len(changes) <= LIVE_MAX_ROWS:
        today = timezone.localdate()
        queryset = with_schedule_details(building_list_queryset().filter(pk__in=changes))
        rows = [prepare_row(building, today) for building in queryset]
    message = ""
    if changes:
        names = sorted({name for people in changes.values() for name in people})
        message = f"{len(changes)} Liegenschaft{'en' if len(changes) > 1 else ''} von {', '.join(names)} geändert"
        if not rows:
            message += " – Seite neu laden zum Anzeigen"
    response = render(request, "buildings/_live.html", {
        "rows": rows, "message": message, "since": now.timestamp(),
    })
    if changes:
        response["HX-Trigger"] = "buildings-changed"  # KPI tiles reload their numbers
    return response
