"""📝 Notes on buildings and installation orders: write, mark erledigt, show at the plan."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from .activity import record
from .models import ActivityKind, Note, NoteKind

MAX_TEXT = 2000


def notes_for(building=None, order=None):
    qs = Note.objects.select_related("author", "resolved_by")
    return qs.filter(building=building) if building is not None else qs.filter(installation_order=order)


def _label(building, order):
    if building is not None:
        return f"AZ {building.file_number} {building.street}"
    return f"{order.re_number} {order.street}"


def _mark(note):
    """In the Verlauf the line already has 📝; only a Storno gets its ⛔ in front."""
    return "⛔ " if note.kind == NoteKind.STORNO else ""


def add_note(user, text, kind=NoteKind.INFO, building=None, order=None):
    if not user.has_perm("journal.add_note"):
        raise PermissionDenied("Notizen schreiben darf deine Rolle nicht.")
    text = (text or "").strip()
    if not text:
        raise ValidationError("Bitte einen Text eingeben.")
    if len(text) > MAX_TEXT:
        raise ValidationError(f"Höchstens {MAX_TEXT} Zeichen.")
    if kind not in NoteKind.values:
        raise ValidationError("Unbekannte Art der Notiz.")
    note = Note.objects.create(building=building, installation_order=order, kind=kind, text=text, author=user)
    record(user, ActivityKind.NOTE, f"{_mark(note)}{note.get_kind_display()} zu {_label(building, order)}: {text[:120]}",
           building=building, order=order)
    return note


def set_resolved(note, user, done=True):
    if not user.has_perm("journal.change_note"):
        raise PermissionDenied("Notizen abhaken darf deine Rolle nicht.")
    note.resolved_at, note.resolved_by = (timezone.now(), user) if done else (None, None)
    note.save(update_fields=["resolved_at", "resolved_by"])
    what = "erledigt" if done else "wieder offen"
    record(user, ActivityKind.NOTE, f"{_mark(note)}{note.get_kind_display()} {what}: {note.text[:120]}",
           building=note.building, order=note.installation_order)
    return note


def open_storno():
    """(building ids, order ids) with an open Storno note - kept out of the suggestions."""
    notes = Note.objects.filter(kind=NoteKind.STORNO, resolved_at=None)
    return (set(notes.exclude(building=None).values_list("building_id", flat=True)),
            set(notes.exclude(installation_order=None).values_list("installation_order_id", flat=True)))


def notes_by_object(building_ids=(), order_ids=()):
    """{("building", id) / ("order", id): [open notes, newest first]} for many objects at once."""
    result = {}
    notes = Note.objects.filter(resolved_at=None).select_related("author")
    for note in notes.filter(building_id__in=list(building_ids)):
        result.setdefault(("building", note.building_id), []).append(note)
    for note in notes.filter(installation_order_id__in=list(order_ids)):
        result.setdefault(("order", note.installation_order_id), []).append(note)
    return result


def attach_notes(stops):
    """stop.notes = open notes of its building AND of its order (preview stops or TourStops)."""
    def ids(stop):
        building = getattr(stop, "building", None)
        order = getattr(stop, "order", None) or getattr(stop, "installation_order", None)
        return (building.pk if building else None), (order.pk if order else None)

    pairs = [ids(stop) for stop in stops]
    found = notes_by_object({b for b, _ in pairs if b}, {o for _, o in pairs if o})
    for stop, (b, o) in zip(stops, pairs):
        stop.notes = sorted(found.get(("building", b), []) + found.get(("order", o), []), key=lambda n: n.created_at,
                            reverse=True)
        stop.storno = any(n.kind == NoteKind.STORNO for n in stop.notes)
    return stops


def note_annotations(field):
    """Annotations for a list: open_notes (number) and has_storno. field: "building" / "installation_order"."""
    from django.db.models import Count, Exists, IntegerField, OuterRef, Subquery, Value
    from django.db.models.functions import Coalesce

    open_notes = Note.objects.filter(**{field: OuterRef("pk")}, resolved_at=None)
    count = open_notes.order_by().values(field).annotate(n=Count("pk")).values("n")
    return {
        "open_notes": Coalesce(Subquery(count, output_field=IntegerField()), Value(0)),
        "has_storno": Exists(open_notes.filter(kind=NoteKind.STORNO)),
    }
