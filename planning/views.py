"""
Tour planning pages (see planning/services.py for the workflow).

All pages work with HTMX:
- the checkbox in a list row posts to select(); the answer is the updated
  "Fahrplan erstellen (n)" button in the page header
- the button opens the dialog (plan_dialog) in #modal
- the preview page reloads only #preview after each change
"""

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from buildings.models import Building

from . import services
from .calendar import calendar_events
from .display import route_sketch
from .forms import DraftSettingsForm, PlanForm
from .models import Employee, Tour, TourStop
from .rules.ordering import STRATEGIES

PLAN_PERMISSION = "planning.add_tour"


def _plan_bar(request):
    return render(request, "planning/_plan_bar.html", services.plan_bar_context(request.session))


@require_POST
@permission_required(PLAN_PERMISSION, raise_exception=True)
def select(request):
    """Checkbox in a row: add or remove the building from the selection."""
    services.toggle_selection(request.session, int(request.POST["building"]), "checked" in request.POST)
    return _plan_bar(request)


@require_POST
@permission_required(PLAN_PERMISSION, raise_exception=True)
def select_clear(request):
    services.clear_selection(request.session)
    response = HttpResponse("")
    response["HX-Refresh"] = "true"  # reload the page so all checkboxes are empty again
    return response


@permission_required(PLAN_PERMISSION, raise_exception=True)
def plan_dialog(request):
    ids = services.get_selection(request.session)
    buildings = list(Building.objects.filter(pk__in=ids).order_by("file_number"))
    form = PlanForm(request.POST or None)
    if request.method == "POST" and form.is_valid() and buildings:
        data = form.cleaned_data
        request.session[services.DRAFT_KEY] = services.create_draft(
            [b.pk for b in buildings], data["employee"], data["date"], data["start"], data["break_minutes"], data["strategy"])
        response = HttpResponse("")
        response["HX-Redirect"] = reverse("planning:draft")
        return response
    return render(request, "planning/_plan_dialog.html", {
        "form": form, "buildings": buildings, "minutes": sum(b.reading_minutes for b in buildings),
    })


def _draft_or_none(request):
    return request.session.get(services.DRAFT_KEY)


def _render_preview(request, draft, template="planning/_preview.html"):
    preview = services.calculate_preview(draft)
    settings_form = DraftSettingsForm(initial={"start": draft["start"], "break_minutes": draft["break"]})
    return render(request, template, {"preview": preview, "draft": draft, "settings_form": settings_form,
                                      "strategies": STRATEGIES, "sketch": route_sketch(preview.stops)})


@permission_required(PLAN_PERMISSION, raise_exception=True)
def draft(request):
    current = _draft_or_none(request)
    if not current:
        messages.info(request, "Kein Fahrplan in Bearbeitung – bitte Liegenschaften auswählen.")
        return redirect("buildings:list")
    return _render_preview(request, current, "planning/draft.html")


@require_POST
@permission_required(PLAN_PERMISSION, raise_exception=True)
def draft_action(request):
    """▲ ▼ ✕, new sorting or new start/break -> recalculate the preview."""
    current = _draft_or_none(request)
    if not current:
        return HttpResponse(status=204)
    action, index = request.POST.get("action"), int(request.POST.get("index", -1))
    if action == "up":
        services.move_stop(current, index, -1)
    elif action == "down":
        services.move_stop(current, index, +1)
    elif action == "remove":
        services.remove_stop(current, index)
    elif action == "sort" and request.POST.get("strategy") in STRATEGIES:
        current["strategy"] = request.POST["strategy"]
        services.sort_draft(current)
    elif action == "settings":
        form = DraftSettingsForm(request.POST)
        if form.is_valid():
            current["start"] = form.cleaned_data["start"].strftime("%H:%M")
            current["break"] = form.cleaned_data["break_minutes"]
    request.session[services.DRAFT_KEY] = current
    return _render_preview(request, current)


@require_POST
@permission_required(PLAN_PERMISSION, raise_exception=True)
def draft_save(request):
    current = _draft_or_none(request)
    if not current:
        return redirect("buildings:list")
    confirm = request.POST.get("confirm") == "1"
    if confirm and not request.user.has_perm("planning.confirm_tour"):
        messages.error(request, "Deine Rolle darf Fahrpläne nicht bestätigen.")
        return redirect("planning:draft")
    try:
        tour = services.save_draft(current, request.user, confirm=confirm)
    except services.ConcurrentChange as error:
        messages.error(request, f"{error} Bitte die Planung neu starten.")
        return redirect("planning:draft")
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("planning:draft")
    del request.session[services.DRAFT_KEY]
    services.clear_selection(request.session)
    state = "bestätigt" if confirm else "vorläufig gespeichert"
    count = tour.stops.count()
    messages.success(request, f"Fahrplan {tour.employee} am {tour.date:%d.%m.%Y} {state} ({count} Stopp{'s' if count != 1 else ''}).")
    # like the prototype: jump to the calendar on that day
    return redirect(f"{reverse('planning:calendar')}?datum={tour.date.isoformat()}")


@require_POST
@permission_required(PLAN_PERMISSION, raise_exception=True)
def draft_discard(request):
    request.session.pop(services.DRAFT_KEY, None)
    messages.info(request, "Planung verworfen – die Auswahl bleibt erhalten.")
    return redirect("buildings:list")


# =============================================================================
# Calendar
# =============================================================================


def _calendar_employees(user):
    """Office roles see everybody; readers only themselves ("nur eigene Termine")."""
    if user.has_perm("planning.view_tour"):
        return Employee.objects.filter(active=True)
    return Employee.objects.filter(user=user)


def _may_see_calendar(user):
    return user.has_perm("planning.view_tour") or user.has_perm("planning.view_own_tours")


@login_required
def calendar_page(request):
    if not _may_see_calendar(request.user):
        return render(request, "403.html", status=403)
    employees = _calendar_employees(request.user)
    return render(request, "planning/calendar.html", {
        "employees": employees,
        "editable": request.user.has_perm("planning.change_tour"),
        "initial_date": request.GET.get("datum", ""),
    })


@login_required
def calendar_feed(request):
    """JSON for FullCalendar: ?start=...&end=...&person=<id>."""
    if not _may_see_calendar(request.user):
        return JsonResponse([], safe=False, status=403)
    start = datetime.date.fromisoformat(request.GET["start"][:10])
    end = datetime.date.fromisoformat(request.GET["end"][:10])
    employees = _calendar_employees(request.user)
    if request.GET.get("person"):
        employees = employees.filter(pk=request.GET["person"])
    editable = request.user.has_perm("planning.change_tour")
    return JsonResponse(calendar_events(start, end, employees, editable), safe=False)


@login_required
def tour_detail(request, pk):
    """Side panel with the stops of one tour (opened by clicking an event)."""
    tour = get_object_or_404(Tour.objects.select_related("employee"), pk=pk)
    if not (request.user.has_perm("planning.view_tour") or tour.employee.user_id == request.user.pk):
        return HttpResponse(status=403)
    stops = TourStop.objects.filter(tour=tour).select_related("building", "installation_order").order_by("position")
    return render(request, "planning/_tour_detail.html", {
        "tour": tour, "stops": stops,
        "employees": Employee.objects.filter(can_read=True, active=True),
    })


@require_POST
@permission_required("planning.change_tour", raise_exception=True)
def tour_move(request, pk):
    """Drag & drop in the calendar, or "Verschieben" in the side panel.

    Nothing is saved here: the tour becomes a draft for the new day, is
    recalculated (TomTom with the new day's traffic) and shown in the preview.
    Only "übernehmen" there changes the tour.
    """
    tour = get_object_or_404(Tour, pk=pk)

    def answer(message=None, status=200):
        # The calendar (fetch) gets JSON; the form in the side panel (HTMX) a redirect or a message.
        if request.htmx:
            if message:
                return render(request, "core/_toast.html", {"message": message, "error": True}, status=200)
            response = HttpResponse("")
            response["HX-Redirect"] = reverse("planning:draft")
            return response
        return JsonResponse({"message": message} if message else {"redirect": reverse("planning:draft")}, status=status)

    if str(tour.version) != request.POST.get("version", str(tour.version)):
        return answer("Der Fahrplan wurde inzwischen geändert – bitte den Kalender neu laden.", 409)
    new_date = datetime.date.fromisoformat(request.POST.get("date") or tour.date.isoformat())
    employee = get_object_or_404(Employee, pk=request.POST["employee"]) if request.POST.get("employee") else tour.employee
    try:
        request.session[services.DRAFT_KEY] = services.draft_from_tour(tour, new_date, employee)
    except ValueError as error:
        return answer(str(error), 400)
    return answer()


@require_POST
@permission_required("planning.change_tour", raise_exception=True)
def tour_recalculate(request, pk):
    """'Neu rechnen / bearbeiten': open the tour as a draft on the same day."""
    tour = get_object_or_404(Tour, pk=pk)
    request.session[services.DRAFT_KEY] = services.draft_from_tour(tour)
    response = HttpResponse("")
    response["HX-Redirect"] = reverse("planning:draft")
    return response


@require_POST
@permission_required("planning.delete_tour", raise_exception=True)
def tour_delete(request, pk):
    tour = get_object_or_404(Tour, pk=pk)
    label = f"{tour.employee} am {tour.date:%d.%m.%Y}"
    services.delete_tour(tour)
    response = render(request, "core/_toast.html", {"message": f"Fahrplan {label} gelöscht"})
    response["HX-Trigger"] = "calendar-changed"  # the calendar reloads its events
    return response
