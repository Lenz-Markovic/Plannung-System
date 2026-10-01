"""🔄 Zwischenablesungen (Nutzerwechsel): announced mostly by mail from the Hausverwaltung."""

import datetime
import json

from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from buildings.models import Building

from . import interim, services
from .models import Employee, InterimReading
from .rules import interim as rules

FILTERS = [("offen", "zu planen"), ("geplant", "📅 geplant"), ("erledigt", "✓ erledigt / storniert"), ("alle", "alle")]


def _context(request, chosen, building=None, form=None, errors=None):
    today = timezone.localdate()
    offered = interim.offered_for(building) if building else []
    return {
        "items": interim.interim_list(chosen, today), "filters": FILTERS, "chosen": chosen, "today": today,
        "building": building, "offered": offered, "defaults": rules.default_needs(offered),
        "sources": rules.SOURCES, "form": form or {}, "errors": errors or [], "may_edit": interim.may_edit(request.user),
        "people": Employee.objects.filter(active=True, can_read=True).order_by("short_name"),
        "need_minutes": json.dumps(rules.NEED_MINUTES), "flat_minutes": rules.FLAT_MINUTES,
    }


@permission_required("planning.view_tour", raise_exception=True)
def interim_page(request):
    chosen = request.GET.get("f", "offen") if request.GET.get("f", "offen") in dict(FILTERS) else "offen"
    building = Building.objects.filter(pk=request.GET.get("liegenschaft")).first() if request.GET.get("liegenschaft", "").isdigit() else None
    context = _context(request, chosen, building)
    if request.htmx_target == "im-list":
        return render(request, "planning/_interim_list.html", context)
    return render(request, "planning/interim.html", context)


@permission_required("planning.view_tour", raise_exception=True)
def interim_search(request):
    """Find the Liegenschaft from the mail (AZ, street, place)."""
    words = request.GET.get("suche", "").split()
    found = Building.objects.all()
    for word in words:
        found = found.filter(Q(file_number__icontains=word) | Q(street__icontains=word) | Q(city__icontains=word)
                             | Q(zip_code__startswith=word))
    return render(request, "planning/_interim_search.html", {"found": found.order_by("file_number")[:8] if words else []})


@require_POST
@permission_required("planning.view_tour", raise_exception=True)
def interim_create(request):
    building = get_object_or_404(Building, pk=request.POST.get("building"))
    units, tenants = request.POST.getlist("unit"), request.POST.getlist("tenant")
    rows = [(u, t, request.POST.getlist(f"needs_{i}")) for i, (u, t) in enumerate(zip(units, tenants))]
    flats = rules.clean_flats(rows)
    try:
        move_date = datetime.date.fromisoformat(request.POST.get("move_date", ""))
    except ValueError:
        move_date = None
    try:
        interim.create(request.user, building, move_date, request.POST.get("source", ""), flats, request.POST.get("note", ""))
    except PermissionDenied:
        raise
    except ValidationError as error:
        context = _context(request, "offen", building, form=request.POST, errors=error.messages)
        context["typed"] = flats
        return render(request, "planning/interim.html", context, status=200)
    return redirect(reverse("planning:interim") + "?f=offen")


@require_POST
@permission_required("planning.add_tour", raise_exception=True)
def interim_plan(request, pk):
    """📅 einplanen: person + day -> "Fahrplan prüfen" with what is already planned that day."""
    item = get_object_or_404(InterimReading, pk=pk)
    employee = get_object_or_404(Employee, pk=request.POST.get("employee"))
    try:
        day = datetime.date.fromisoformat(request.POST.get("date", ""))
        draft = interim.plan_draft(item, employee, day)
    except (ValueError, ValidationError) as error:
        message = " ".join(error.messages) if isinstance(error, ValidationError) else "Bitte einen Tag wählen."
        response = render(request, "core/_toast.html", {"message": message, "error": True})
        response["HX-Reswap"] = "none"
        return response
    request.session[services.DRAFT_KEY] = draft
    response = HttpResponse("")
    response["HX-Redirect"] = reverse("planning:draft")
    return response


@require_POST
@permission_required("planning.view_tour", raise_exception=True)
def interim_cancel(request, pk):
    item = get_object_or_404(InterimReading, pk=pk)
    interim.cancel(item, request.user)
    return redirect(reverse("planning:interim") + "?f=" + request.POST.get("f", "offen"))
