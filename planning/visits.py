"""
Termin-Ergebnis ("what happened, what comes after") - database side of rules/visits.py.

- report(): the Ableser/Monteur reports ✓ fertig / ◐ teilweise / ✗ nicht erledigt in Mein Tag
- a Visit stays for good: 1. Termin, 2. Termin (Nachtermin) ... with what is still to do
- revisit_objects(): buildings / orders that need a Nachtermin (not planned again yet)
- attach_attempts(): "2. Termin" + what happened last time, for stops being planned / shown
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from .models import StopKind, TourStatus, TourStop, Visit
from .rules.visits import (ABSENT, COMPLETE, OUTCOMES, PARTIAL, REASON_LABELS, VisitInfo, attempt_number,
                           needs_revisit, report_problems)


def _target(stop):
    """(building, order) the visit belongs to: a reading -> building, an installation -> order."""
    if stop.kind == StopKind.INSTALLATION or (stop.kind == StopKind.HELP and stop.installation_order_id):
        return None, stop.installation_order
    return stop.building, None


def _visits_of(building, order):
    return Visit.objects.filter(building=building) if building else Visit.objects.filter(installation_order=order)


def _update_tour_status(tour):
    all_done = not tour.stops.filter(done_at__isnull=True).exists()
    if all_done and tour.status != TourStatus.DONE:
        tour.status = TourStatus.DONE
        tour.save()
    elif not all_done and tour.status == TourStatus.DONE:
        tour.status = TourStatus.CONFIRMED if tour.confirmed_at else TourStatus.PROVISIONAL
        tour.save()


def report(stop, user, outcome, todo="", reason=""):
    """Save the Ergebnis of a stop. Returns the Visit (None for a help stop)."""
    from .dayplan import may_work_on

    if not user.has_perm("planning.mark_stop_done") or not may_work_on(user, stop.tour):
        raise PermissionDenied("Diesen Stopp darfst du nicht melden.")
    problems = report_problems(outcome, todo, reason)
    if problems:
        raise ValidationError(problems[0])
    stop.outcome, stop.done_at, stop.done_by = outcome, timezone.now(), user
    stop.save()
    _update_tour_status(stop.tour)
    if stop.kind == StopKind.HELP:
        return None  # the helped plan has the visit
    building, order = _target(stop)
    earlier = _visits_of(building, order).exclude(stop=stop).values_list("date", flat=True)
    visit, _ = Visit.objects.update_or_create(stop=stop, defaults={
        "building": building, "installation_order": order, "kind": stop.kind, "date": stop.tour.date,
        "tour": stop.tour, "people": stop.tour.people_label, "attempt": attempt_number(list(earlier), stop.tour.date),
        "outcome": outcome, "reason": reason if outcome == ABSENT else "",
        "todo": todo.strip() if outcome != COMPLETE else "", "note": stop.field_note, "reported_by": user,
    })
    return visit


def undo(stop, user):
    """'↺ zurücksetzen': the stop is open again, its Visit is gone."""
    from .dayplan import may_work_on

    if not user.has_perm("planning.mark_stop_done") or not may_work_on(user, stop.tour):
        raise PermissionDenied("Diesen Stopp darfst du nicht ändern.")
    Visit.objects.filter(stop=stop).delete()
    stop.outcome, stop.done_at, stop.done_by = "", None, None
    stop.save()
    _update_tour_status(stop.tour)


def close(visit, user, closed=True):
    """Office: 'kein Nachtermin nötig' (e.g. settled on the phone) - or open it again."""
    if not user.has_perm("planning.change_tour"):
        raise PermissionDenied("Das darf deine Rolle nicht.")
    visit.closed_at, visit.closed_by = (timezone.now(), user) if closed else (None, None)
    visit.save(update_fields=["closed_at", "closed_by"])
    return visit


def _planned_dates(building_ids, order_ids):
    """{("building"/"order", id): [days of planned, not yet visited stops]}."""
    found = {}
    stops = (TourStop.objects.filter(done_at__isnull=True, outcome="").exclude(kind=StopKind.HELP)
             .values_list("kind", "building_id", "installation_order_id", "tour__date"))
    for kind, building, order, date in stops:
        key = ("order", order) if kind == StopKind.INSTALLATION else ("building", building)
        if (key[0] == "order" and order in order_ids) or (key[0] == "building" and building in building_ids):
            found.setdefault(key, []).append(date)
    return found


def revisit_objects():
    """{("building", id) / ("order", id): last Visit} that need a Nachtermin (not planned again yet)."""
    visits = {}
    for visit in Visit.objects.select_related("reported_by").order_by("date", "pk"):
        key = ("order", visit.installation_order_id) if visit.installation_order_id else ("building", visit.building_id)
        visits.setdefault(key, []).append(visit)
    planned = _planned_dates({k[1] for k in visits if k[0] == "building"}, {k[1] for k in visits if k[0] == "order"})
    result = {}
    for key, items in visits.items():
        infos = [VisitInfo(v.date, v.outcome, v.closed_at is not None) for v in items]
        if needs_revisit(infos, planned.get(key, [])):
            result[key] = items[-1]
    return result


def revisit_ids():
    """(building ids, order ids) that need a Nachtermin."""
    found = revisit_objects()
    return ({pk for kind, pk in found if kind == "building"}, {pk for kind, pk in found if kind == "order"})


def attach_attempts(stops, date):
    """stop.attempt (1, 2, 3 ...) and stop.last_visit (what happened last time) for these stops."""
    def key(stop):
        building = getattr(stop, "building", None)
        order = getattr(stop, "order", None) or getattr(stop, "installation_order", None)
        if getattr(stop, "kind", "") == StopKind.INSTALLATION and order is not None:
            return ("order", order.pk)
        return ("building", building.pk) if building is not None else ("order", order.pk if order else None)

    keys = [key(s) for s in stops]
    building_ids = [pk for kind, pk in keys if kind == "building" and pk]
    order_ids = [pk for kind, pk in keys if kind == "order" and pk]
    visits = {}
    for visit in (Visit.objects.filter(building_id__in=building_ids) | Visit.objects.filter(installation_order_id__in=order_ids)
                  ).select_related("reported_by").order_by("date", "pk"):
        k = ("order", visit.installation_order_id) if visit.installation_order_id else ("building", visit.building_id)
        visits.setdefault(k, []).append(visit)
    for stop, k in zip(stops, keys):
        own = getattr(stop, "pk", None)
        earlier = [v for v in visits.get(k, []) if v.date < date and v.stop_id != own]
        stop.attempt = len(earlier) + 1
        stop.last_visit = earlier[-1] if earlier else None
        stop.visit = next((v for v in visits.get(k, []) if own and v.stop_id == own), None)
    return stops


OUTCOME_LABELS = OUTCOMES
REASONS = REASON_LABELS
__all__ = ["report", "undo", "close", "revisit_objects", "revisit_ids", "attach_attempts", "COMPLETE", "PARTIAL",
           "ABSENT"]


def visit_annotations(field):
    """For a list (field "building" / "installation_order"): visit_count, last_outcome, last_visit_date,
    last_closed, revisit_planned - and with NEEDS_REVISIT the filter 'Nachtermin nötig'."""
    from django.db.models import Count, Exists, IntegerField, OuterRef, Subquery, Value
    from django.db.models.functions import Coalesce

    visits = Visit.objects.filter(**{field: OuterRef("pk")})
    last = visits.order_by("-date", "-pk")
    count = visits.order_by().values(field).annotate(n=Count("pk")).values("n")
    kind = StopKind.INSTALLATION if field == "installation_order" else StopKind.READING
    planned_after = TourStop.objects.filter(**{field: OuterRef("pk")}, kind=kind, done_at__isnull=True, outcome="",
                                            tour__date__gte=OuterRef("last_visit_date"))
    return {
        "visit_count": Coalesce(Subquery(count, output_field=IntegerField()), Value(0)),
        "last_outcome": Subquery(last.values("outcome")[:1]),
        "last_visit_date": Subquery(last.values("date")[:1]),
        "last_closed": Subquery(last.values("closed_at")[:1]),
        "revisit_planned": Exists(planned_after),
    }


def needs_revisit_q():
    from django.db.models import Q
    return Q(last_outcome__in=[PARTIAL, ABSENT], last_closed__isnull=True, revisit_planned=False)


def object_history(building=None, order=None):
    """For the 🧾 Bearbeitung box: visits (1., 2. ...), planned next stops, and whether a Nachtermin is needed."""
    items = list(_visits_of(building, order).select_related("reported_by", "closed_by").order_by("date", "pk"))
    kind = StopKind.READING if building is not None else StopKind.INSTALLATION
    target = {"building": building} if building is not None else {"installation_order": order}
    planned = list(TourStop.objects.filter(**target, kind=kind, done_at__isnull=True, outcome="")
                   .select_related("tour__employee").order_by("tour__date"))
    infos = [VisitInfo(v.date, v.outcome, v.closed_at is not None) for v in items]
    last = items[-1] if items else None
    for stop in planned:
        stop.attempt = attempt_number([v.date for v in items], stop.tour.date)
    return {
        "visits": items, "planned": planned, "last": last,
        "needs_revisit": needs_revisit(infos, [s.tour.date for s in planned]),
        "next_attempt": len(items) + 1,
    }
