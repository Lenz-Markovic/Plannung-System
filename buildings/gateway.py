"""📡 Gateway check - database side of buildings/rules/gateway.py.

The office enters what the gateway received; the state says what comes next (freigeben, try from
outside, appointment, wait for the values of the devices without radio).
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.utils import timezone

from .models import Building, BuildingStatus
from .rules import gateway as rules

GATEWAY_Q = Q(has_gateway=True) | Q(installation_type="radio_gateway")


def gateway_buildings():
    return Building.objects.filter(GATEWAY_Q)


def facts(buildings):
    """{building pk: GatewayFacts} - with the visits and open stops after the check."""
    from planning.models import StopKind, TourStop, Visit

    buildings = list(buildings)
    pks = [b.pk for b in buildings]
    last = {}
    for visit in Visit.objects.filter(building_id__in=pks).order_by("date", "pk"):
        last.setdefault(visit.building_id, []).append(visit)
    planned = {}
    for stop in TourStop.objects.filter(building_id__in=pks, kind=StopKind.READING, done_at__isnull=True,
                                        outcome="").select_related("tour"):
        planned.setdefault(stop.building_id, []).append(stop.tour.date)
    found = {}
    for b in buildings:
        since = b.gateway_checked_on
        after = [v for v in last.get(b.pk, []) if since and v.date >= since]
        found[b.pk] = rules.GatewayFacts(
            checked=since is not None, total=b.gateway_total, received=b.gateway_received,
            manual_devices=b.gateway_manual_devices, manual_state=b.gateway_manual_state,
            last_visit=after[-1].outcome if after else "",
            planned=any(since and d >= since for d in planned.get(b.pk, [])),
            released=b.status == BuildingStatus.RELEASED)
    return found


def states(buildings):
    """{building pk: state} for gateway buildings (others are left out)."""
    gateways = [b for b in buildings if b.is_gateway]
    return {pk: rules.gateway_state(f) for pk, f in facts(gateways).items()}


def state_of(building):
    return states([building]).get(building.pk, rules.NOT_GATEWAY)


def no_visit_ids():
    """Gateway buildings that need no appointment now (not checked, complete, values coming, released):
    they are not suggested for planning. A 🔁 Nachtermin (something open after a visit) is always suggested."""
    from planning.visits import revisit_ids

    open_after_visit = revisit_ids()[0]
    return {pk for pk, state in states(gateway_buildings()).items()
            if not rules.needs_visit(state) and state != rules.PLANNED and pk not in open_after_visit}


def outside_try(building):
    """A new reading stop of this building is a try from outside (only the missing devices, no appointment)."""
    return building.is_gateway and state_of(building) == rules.GAP


def save_check(building, user, total, received, missing_note="", manual_devices=0, manual_state=""):
    if not user.has_perm("buildings.change_building"):
        raise PermissionDenied("Die Gateway-Prüfung darf deine Rolle nicht eintragen.")
    problems = rules.check_problems(total, received, manual_devices)
    if manual_state not in dict(rules.MANUAL_STATES):
        problems.append("Unbekannter Stand der Werte ohne Funk.")
    if problems:
        raise ValidationError(problems[0])
    building.gateway_checked_on = timezone.localdate()
    building.gateway_checked_by = user
    building.gateway_total, building.gateway_received = total, received
    building.gateway_missing_note = (missing_note or "").strip()[:300]
    building.gateway_manual_devices = manual_devices or 0
    building.gateway_manual_state = manual_state if building.gateway_manual_devices else ""
    building.save()
    from journal.activity import record
    from journal.models import ActivityKind

    gap = rules.missing(total, received)
    text = (f"📡 Gateway geprüft: AZ {building.file_number} {building.street}: {received} von {total} empfangen"
            + (f" – {gap} fehlen{': ' + building.gateway_missing_note if building.gateway_missing_note else ''}" if gap else " (100 %)")
            + (f" · {building.gateway_manual_devices} ohne Funk ({building.get_gateway_manual_state_display()})"
               if building.gateway_manual_devices else ""))
    record(user, ActivityKind.OFFICE, text, building=building)
    return state_of(building)
