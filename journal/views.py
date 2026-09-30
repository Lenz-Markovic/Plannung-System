"""📝 Notes box (for a building or an order) and 🕘 the Verlauf side drawer."""

import datetime
import re

from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from buildings.models import Building, InstallationOrder

from . import notes
from .models import Activity, ActivityKind, Note, NoteKind

TARGETS = {"liegenschaft": Building, "auftrag": InstallationOrder}


def _target(target, pk):
    model = TARGETS.get(target)
    if model is None:
        raise Http404
    obj = get_object_or_404(model, pk=pk)
    return (obj, None) if model is Building else (None, obj)


def _box(request, target, pk, message="", error=False, in_modal=False):
    building, order = _target(target, pk)
    context = {
        "target": target, "pk": pk, "obj": building or order, "is_building": building is not None,
        "notes": list(notes.notes_for(building, order)), "kinds": NoteKind.choices, "in_modal": in_modal,
    }
    response = render(request, "journal/_notes_modal.html" if in_modal else "journal/_notes.html", context)
    if message:
        response.write(render(request, "core/_toast.html", {"message": message, "error": error}).content)
    return response


@permission_required("journal.view_note", raise_exception=True)
def notes_box(request, target, pk):
    """GET: the box (in a detail row) or, with ?dialog=1, the pop-up (from a plan)."""
    return _box(request, target, pk, in_modal=request.GET.get("dialog") == "1")


@require_POST
@permission_required("journal.view_note", raise_exception=True)
def note_add(request, target, pk):
    building, order = _target(target, pk)
    in_modal = request.POST.get("dialog") == "1"
    try:
        note = notes.add_note(request.user, request.POST.get("text", ""), request.POST.get("kind", NoteKind.INFO),
                              building=building, order=order)
    except (ValidationError, PermissionDenied) as error:
        response = render(request, "core/_toast.html",
                          {"message": error.messages[0] if hasattr(error, "messages") else str(error), "error": True})
        response["HX-Reswap"] = "none"
        return response
    response = _box(request, target, pk, f"{note.get_kind_display()} gespeichert", in_modal=in_modal)
    response["HX-Trigger"] = "notes-changed"
    return response


@require_POST
@permission_required("journal.view_note", raise_exception=True)
def note_resolve(request, pk):
    note = get_object_or_404(Note, pk=pk)
    try:
        notes.set_resolved(note, request.user, done=request.POST.get("done") == "1")
    except PermissionDenied as error:
        response = render(request, "core/_toast.html", {"message": str(error), "error": True})
        response["HX-Reswap"] = "none"
        return response
    target, obj_pk = ("liegenschaft", note.building_id) if note.building_id else ("auftrag", note.installation_order_id)
    response = _box(request, target, obj_pk, "erledigt" if note.resolved_at else "wieder offen",
                    in_modal=request.POST.get("dialog") == "1")
    response["HX-Trigger"] = "notes-changed"
    return response


# --- 🕘 Verlauf ----------------------------------------------------------------------------

AREAS = [("", "Alle"), (ActivityKind.PLAN, "🗺 Fahrpläne"), (ActivityKind.NOTICE, "📄 Aushänge"),
         (ActivityKind.FIELD, "📱 vor Ort"), (ActivityKind.OFFICE, "🧾 Bearbeitung"), (ActivityKind.ORDER, "🔧 Aufträge"), (ActivityKind.STATUS, "🏷 Status"), (ActivityKind.NOTE, "📝 Notizen"),
         (ActivityKind.DOCUMENTS, "📥 Unterlagen")]
PAGE = 60
DAY_CHOICES = ("1", "7", "30")


@permission_required("journal.view_activity", raise_exception=True)
def activity_drawer(request):
    """The side drawer 'Verlauf': newest first, filter by area / only mine / one plan or object."""
    entries = Activity.objects.select_related("user", "tour__employee")
    area = request.GET.get("bereich", "")
    if area:
        entries = entries.filter(kind=area)
    if request.GET.get("wer") == "ich":
        entries = entries.filter(user=request.user)
    scope = {}
    for key in ("tour", "liegenschaft", "auftrag"):
        value = request.GET.get(key, "")
        if re.fullmatch(r"[0-9]{1,12}", value):  # anything else is ignored (no 500 for odd links)
            scope[key] = int(value)
    if "tour" in scope:
        entries = entries.filter(tour_id=scope["tour"])
    if "liegenschaft" in scope:  # the object itself, and every plan it is (or was) in
        entries = entries.filter(Q(building_id=scope["liegenschaft"]) | Q(tour__stops__building_id=scope["liegenschaft"]))
    if "auftrag" in scope:
        entries = entries.filter(Q(installation_order_id=scope["auftrag"])
                                 | Q(tour__stops__installation_order_id=scope["auftrag"]))
    days = request.GET.get("tage", "")
    if days in DAY_CHOICES:
        entries = entries.filter(created_at__gte=timezone.now() - datetime.timedelta(days=int(days)))
    else:
        days = ""
    entries = list(entries.distinct()[:PAGE])
    for entry in entries:
        entry.local_day = timezone.localdate(entry.created_at)  # grouped by the day here, not by UTC
    template = "journal/_activity_list.html" if request.htmx_target == "activity-list" else "journal/_activity.html"
    return render(request, template, {
        "entries": entries, "areas": AREAS, "area": area, "mine": request.GET.get("wer") == "ich",
        "days": days, "scope": scope,
        "today": timezone.localdate(),
    })


