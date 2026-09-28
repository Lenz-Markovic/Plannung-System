"""
Tour planning pages (see planning/services.py for the workflow).

All pages work with HTMX:
- the checkbox in a list row posts to select(); the answer is the updated
  "Fahrplan erstellen (n)" button in the page header
- the button opens the dialog (plan_dialog) in #modal
- the preview page reloads only #preview after each change
"""

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from buildings.models import Building

from . import services
from .display import route_sketch
from .forms import DraftSettingsForm, PlanForm
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
    messages.success(request, f"Fahrplan {tour.employee} am {tour.date:%d.%m.%Y} {state} ({tour.stops.count()} Stopps).")
    return redirect("buildings:list")


@require_POST
@permission_required(PLAN_PERMISSION, raise_exception=True)
def draft_discard(request):
    request.session.pop(services.DRAFT_KEY, None)
    messages.info(request, "Planung verworfen – die Auswahl bleibt erhalten.")
    return redirect("buildings:list")
