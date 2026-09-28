"""
Conflict check: collect the facts from the database, run the pure rules
(conflicts/rules.py) and store the result in the Conflict table.

Called after every change that can create or solve a conflict: saving,
moving or deleting a tour, changing an order, the import. All users then
see the same list ("⚠ Konflikte" page, badges in the lists).
"""

from django.core.exceptions import PermissionDenied, ValidationError
import dataclasses

from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from buildings.models import Building, InstallationOrder, OrderStatus
from buildings.rules.file_numbers import extract_re_numbers
from planning.models import StopKind, TourStop

from . import rules
from .models import Conflict

STOPS = TourStop.objects.select_related("tour__employee").order_by("tour__date", "start_time")


# --- which orders belong to which building (same as reindexOne() in the prototype) ---

def matching_orders(building, orders_by_re, orders_by_core):
    """[(order, via)]: first via the RE numbers in the building's notes, then via the building number."""
    found = {}
    for re_number in sorted(extract_re_numbers(building.order_reference, building.handwritten_note)):
        order = orders_by_re.get(re_number)
        if order:
            found[order.re_number] = (order, "RE-Nr.")
    for order in orders_by_core.get(building.file_number_core, []):
        found.setdefault(order.re_number, (order, "Liegenschaftsnummer"))
    return list(found.values())


def _order_index(orders):
    by_re, by_core = {}, {}
    for order in orders:
        by_re[order.re_number.upper()] = order
        if order.building_file_number_core:
            by_core.setdefault(order.building_file_number_core, []).append(order)
    return by_re, by_core


def _orders():
    return list(InstallationOrder.objects.prefetch_related(
        Prefetch("tour_stops", queryset=STOPS.filter(kind=StopKind.INSTALLATION), to_attr="planned"),
        "items__category", "assigned_installers",
    ).order_by("re_number"))


# --- facts for the rules ------------------------------------------------------------

def reading_facts(building):
    stop = building.planned[0] if building.planned else None
    return rules.Reading(
        date=stop.tour.date if stop else None,
        reader=stop.tour.employee.short_name if stop else "",
        reading_type=building.reading_type,
        minutes=building.reading_minutes_calculated,
        has_gateway=building.has_gateway,
    )


def order_facts(order, via):
    stop = order.planned[0] if order.planned else None
    installers = sorted(e.short_name for e in order.assigned_installers.all())
    kinds = []
    for item in order.items.all():
        if item.category and item.category.code not in kinds:
            kinds.append(item.category.code)
    return rules.Order(
        re_number=order.re_number, via=via,
        date=stop.tour.date if stop else None,
        time=(stop.start_time or stop.tour.start_time) if stop else None,
        installer=stop.tour.employee.short_name if stop else (installers[0] if installers else ""),
        done=order.status == OrderStatus.DONE,
        kinds=tuple(kinds), summary=order.summary,
    )


def evaluate(buildings, orders=None):
    """{building: [(Message, order, reading stop, installation stop)]} - nothing is saved."""
    orders = _orders() if orders is None else orders
    by_re, by_core = _order_index(orders)
    buildings = buildings.prefetch_related(
        Prefetch("tour_stops", queryset=STOPS.filter(kind=StopKind.READING), to_attr="planned"))
    result = {}
    for building in buildings:
        matches = matching_orders(building, by_re, by_core)
        order_of = {order.re_number: order for order, _ in matches}
        messages = rules.evaluate_installations(reading_facts(building), [order_facts(o, via) for o, via in matches])
        reading_stop = building.planned[0] if building.planned else None
        result[building] = [
            (m, order_of[m.re_number], reading_stop, (order_of[m.re_number].planned or [None])[0]) for m in messages
        ]
    return result


def installation_findings(order_ids, date, installer):
    """What-if check for the plan preview: these orders on `date` by `installer`.

    {order id: [(severity, text)]} with the critical / warning messages only
    (like konflikteFuer() in the prototype, which adds them as "Montage: ...").
    """
    wanted = set(order_ids)
    if not wanted:
        return {}
    orders = _orders()
    by_re, by_core = _order_index(orders)
    buildings = buildings_for(order_ids=wanted).prefetch_related(
        Prefetch("tour_stops", queryset=STOPS.filter(kind=StopKind.READING), to_attr="planned"))
    found = {pk: [] for pk in wanted}
    for building in buildings:
        for order, via in matching_orders(building, by_re, by_core):
            if order.pk not in wanted:
                continue
            facts = dataclasses.replace(order_facts(order, via), date=date, time=None, installer=installer, done=False)
            for message in rules.evaluate_installations(reading_facts(building), [facts]):
                if message.severity in (rules.CRITICAL, rules.WARNING):
                    found[order.pk].append((message.severity, f"AZ {building.file_number}: {message.text}"))
    return found


# --- store ----------------------------------------------------------------------------

@transaction.atomic
def refresh_conflicts(buildings=None):
    """Recalculate and store the conflicts of these buildings (default: all).

    Rows are matched by (rule, building, order): a conflict that still exists
    keeps its "bewusst übernommen" note; solved conflicts are deleted.
    Returns the number of open (not acknowledged) conflicts written.
    """
    buildings = Building.objects.all() if buildings is None else buildings
    evaluated = evaluate(buildings)
    existing = {(c.rule, c.building_id, c.installation_order_id): c
                for c in Conflict.objects.filter(building__in=list(evaluated))}
    keep = set()
    for building, items in evaluated.items():
        for message, order, reading_stop, installation_stop in items:
            if message.severity == rules.OK:
                continue
            key = (message.rule, building.pk, order.pk)
            if key in keep:
                continue  # the same rule twice for one order (cannot happen today, but be safe)
            keep.add(key)
            conflict = existing.get(key) or Conflict(rule=message.rule, building=building, installation_order=order)
            changed = conflict.message != message.text or conflict.severity != message.severity
            conflict.severity, conflict.message = message.severity, message.text
            conflict.stop, conflict.other_stop = installation_stop, reading_stop
            if changed and conflict.acknowledged_at:
                # the situation changed (e.g. new date): the old "bewusst übernommen" no longer fits
                conflict.acknowledged_by, conflict.acknowledged_at, conflict.acknowledged_note = None, None, ""
            conflict.save()
    Conflict.objects.filter(pk__in=[c.pk for key, c in existing.items() if key not in keep]).delete()
    return len(keep)


def buildings_for(building_ids=(), order_ids=()):
    """Buildings affected by a change of these buildings and orders."""
    orders = InstallationOrder.objects.filter(pk__in=order_ids)
    cores = [o.building_file_number_core for o in orders if o.building_file_number_core]
    re_numbers = [o.re_number for o in orders]
    by_re = Q()
    for re_number in re_numbers:
        by_re |= Q(order_reference__icontains=re_number) | Q(handwritten_note__icontains=re_number)
    query = Q(pk__in=building_ids) | Q(file_number_core__in=cores)
    if re_numbers:
        query |= by_re
    # also the buildings sharing a building number core with the changed ones
    cores_of_buildings = Building.objects.filter(pk__in=building_ids).values("file_number_core")
    return Building.objects.filter(query | Q(file_number_core__in=cores_of_buildings))


def refresh_for(building_ids=(), order_ids=()):
    return refresh_conflicts(buildings_for(building_ids, order_ids))


def acknowledge(conflict, user, note):
    """A dispatcher knowingly accepts a conflict ("bewusst übernehmen")."""
    if not user.has_perm("conflicts.acknowledge_conflict"):
        raise PermissionDenied("Konflikte bewusst übernehmen darf deine Rolle nicht.")
    note = note.strip()
    if not note:
        raise ValidationError("Bitte kurz begründen, warum der Konflikt so bleiben darf.")
    conflict.acknowledged_by, conflict.acknowledged_at, conflict.acknowledged_note = user, timezone.now(), note[:300]
    conflict.save()
    return conflict


def reopen(conflict, user):
    if not user.has_perm("conflicts.acknowledge_conflict"):
        raise PermissionDenied
    conflict.acknowledged_by, conflict.acknowledged_at, conflict.acknowledged_note = None, None, ""
    conflict.save()
    return conflict
