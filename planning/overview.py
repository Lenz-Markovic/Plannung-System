"""
📊 Übersicht - database side of rules/overview.py: collects the numbers for the dashboard.
Everything is counted on every page load (nothing extra is stored).
"""

from django.db.models import Count, Min

from buildings.models import Building, BuildingStatus, InstallationOrder, OrderStatus

from .models import StopKind, TourStop, Visit
from .rules import overview as rules
from .rules.visits import REASON_LABELS

OUTCOME_LABELS = {rules.COMPLETE: "✓ fertig", rules.PARTIAL: "◐ teilweise", rules.ABSENT: "✗ nicht erledigt"}


def _kind_filter(queryset, kind, field="kind"):
    if kind == "reading":
        return queryset.filter(**{field: StopKind.READING})
    if kind == "installation":
        return queryset.filter(**{field: StopKind.INSTALLATION})
    return queryset


def season_start():
    first = Visit.objects.aggregate(d=Min("date"))["d"]
    planned = TourStop.objects.aggregate(d=Min("tour__date"))["d"]
    return min([d for d in (first, planned) if d], default=None)


def collect(period, kind, today):
    start, end = rules.period_range(period, today, season_start())
    visits = _kind_filter(Visit.objects.filter(date__gte=start, date__lte=end), kind)
    rows = list(visits.values_list("date", "outcome", "reason", "attempt", "people"))
    stops = _kind_filter(TourStop.objects.filter(tour__date__gte=start, tour__date__lte=end).exclude(kind=StopKind.HELP),
                         kind).select_related("tour__employee").prefetch_related("tour__team")

    per_bucket = rules.visits_per_bucket(start, end, [(r[0], r[1]) for r in rows])
    top, ticks = rules.nice_max(max((b.total for b in per_bucket), default=0))
    columns = [{"bucket": b, "segments": [(o, OUTCOME_LABELS[o], getattr(b, o), 100 * getattr(b, o) / top)
                                          for o in rules.OUTCOME_ORDER if getattr(b, o)]}
               for b in per_bucket]
    totals = {o: sum(1 for r in rows if r[1] == o) for o in rules.OUTCOME_ORDER}
    reported = sum(totals.values())
    reasons = rules.count_reasons([r[2] for r in rows if r[1] == rules.ABSENT], REASON_LABELS)
    attempts = rules.attempts_needed([r[3] for r in rows if r[1] == rules.COMPLETE])
    people = rules.per_person([(s.tour.people_label, bool(s.done_at or s.outcome)) for s in stops if s.tour.date <= today],
                              [(r[4], r[1]) for r in rows])

    status = dict(Building.objects.values_list("status").annotate(n=Count("pk")))
    orders = dict(InstallationOrder.objects.values_list("status").annotate(n=Count("pk")))
    return {
        "start": start, "end": end, "per_week": bool(per_bucket and per_bucket[0].per_week),
        "columns": columns, "label_every": max(1, -(-len(columns) // 12)), "label_every_narrow": max(1, -(-len(columns) // 4)), "ticks": [(t, 100 * t / top) for t in ticks], "top": top,
        "totals": [(o, OUTCOME_LABELS[o], totals[o], rules.percent(totals[o], reported)) for o in rules.OUTCOME_ORDER],
        "reported": reported,
        "reasons": [(label, n, 100 * n / reasons[0][1]) for label, n in reasons] if reasons else [],
        "attempts": [(label, n, 100 * n / max(attempts.values())) for label, n in attempts.items()] if any(attempts.values()) else [],
        "attempts_total": sum(attempts.values()),
        "people": people,
        "people_max": max((p.planned for p in people), default=0),
        "buildings": rules.share_segments([(s.value, s.label, status.get(s.value, 0)) for s in BuildingStatus]),
        "buildings_total": sum(status.values()),
        "released_share": rules.percent(status.get(BuildingStatus.RELEASED, 0), sum(status.values())),
        "orders": [(s.value, s.label, orders.get(s.value, 0)) for s in OrderStatus],
        "orders_max": max(orders.values(), default=0),
        "orders_total": sum(orders.values()),
    }


def headline(today):
    """The key numbers (stat tiles) - independent of the time range: what is open NOW."""
    from conflicts.views import open_count as open_conflicts
    from documents.services import deadline_entries
    from documents.views import _counts

    from . import followup, visits

    buildings, orders = visits.revisit_ids()
    deadlines = _counts(deadline_entries(today, refresh=False))
    return {
        "followup_open": followup.open_count(today),
        "revisits": len(buildings) + len(orders),
        "overdue": deadlines["overdue"], "soon": deadlines["soon"],
        "conflicts": open_conflicts(),
        "planned_today": TourStop.objects.filter(tour__date=today).exclude(kind=StopKind.HELP).count(),
    }
