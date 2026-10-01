"""🔄 Zwischenablesung - database side of planning/rules/interim.py."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from . import services
from .models import InterimReading, StopKind
from .rules import interim as rules


def may_edit(user):
    return user.has_perm("planning.add_tour") or user.has_perm("planning.process_visit")


def offered_for(building):
    return rules.offered_needs(building.hkv_count, building.hkv_family == "Verdunster", building.wwz_count,
                               building.kwz_count, building.wmz_count, building.has_rwm or building.rwm_count)


def decorate(items, today=None):
    """state, the latest stop, labels for the page."""
    today = today or timezone.localdate()
    for item in items:
        stops = sorted(item.stops.all(), key=lambda s: (s.tour.date, s.pk))
        item.stop = stops[-1] if stops else None
        item.state = rules.state(item.cancelled_at is not None, item.stop.outcome if item.stop else "", bool(stops))
        item.state_label = rules.STATE_LABELS[item.state]
        item.due = rules.due_hint(item.move_date, today)
        item.day = rules.suggested_day(item.move_date, today)
        item.flat_rows = [{**f, "labels": [rules.NEED_LABELS.get(n, n) for n in f["needs"]]} for f in item.flats]
    return items


def interim_list(chosen="offen", today=None):
    items = decorate(list(InterimReading.objects.select_related("building", "created_by")
                          .prefetch_related("stops__tour__employee")), today)
    if chosen == "offen":
        return [i for i in items if i.state in (rules.OPEN, rules.PARTIAL)]
    if chosen == "geplant":
        return [i for i in items if i.state == rules.PLANNED]
    if chosen == "erledigt":
        return [i for i in items if i.state in (rules.DONE, rules.CANCELLED)]
    return items


def create(user, building, move_date, source, flats, note=""):
    if not may_edit(user):
        raise PermissionDenied("Zwischenablesungen anlegen darf deine Rolle nicht.")
    problems = rules.problems(flats, move_date)
    if source not in dict(rules.SOURCES):
        problems.append("Bitte angeben, wie es gemeldet wurde.")
    if problems:
        raise ValidationError(problems)
    item = InterimReading.objects.create(building=building, move_date=move_date, source=source, flats=flats,
                                         note=(note or "").strip(), created_by=user)
    from journal.activity import record
    from journal.models import ActivityKind

    record(user, ActivityKind.OFFICE, f"🔄 Zwischenablesung angelegt: AZ {building.file_number} {building.street}, "
           f"Nutzerwechsel {move_date:%d.%m.%Y}: {item.units_text}", building=building)
    return item


def cancel(item, user):
    if not may_edit(user):
        raise PermissionDenied("Stornieren darf deine Rolle nicht.")
    item.cancelled_at = timezone.now()
    item.save(update_fields=["cancelled_at", "updated_at"])
    from journal.activity import record
    from journal.models import ActivityKind

    record(user, ActivityKind.OFFICE, f"🔄 Zwischenablesung storniert: AZ {item.building.file_number} "
           f"{item.building.street} ({item.units_text})", building=item.building)


def plan_draft(item, employee, date, start=None):
    """A draft for that person and day (with what is already planned there) plus this Zwischenablesung."""
    if item.cancelled_at:
        raise ValidationError("Diese Zwischenablesung ist storniert.")
    draft = services.create_draft([], employee, date, start or employee.default_start_time, 30, "far")
    new = {"kind": StopKind.READING, "building": item.building_id, "interim": item.pk}
    if services._stop_key(new) not in {services._stop_key(s) for s in draft["stops"]}:
        draft["stops"].append(new)
    return services.sort_draft(draft)
