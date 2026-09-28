"""
Database queries for the building list.

"Selectors" are functions that only READ data. Keeping them here (and not
in the view) means the view stays short and the queries can be reused,
e.g. later for the Excel export.
"""

from django.db.models import Case, Count, Exists, IntegerField, OuterRef, Prefetch, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce

from planning.models import StopKind, TourStop

from .models import Building, BuildingStatus, InstallationOrder, SourceSystem

# Order of the status column when sorting (as STAT in the prototype)
STATUS_RANK = Case(
    When(status=BuildingStatus.OPEN, then=Value(0)),
    When(status=BuildingStatus.REWORK, then=Value(1)),
    When(status=BuildingStatus.RELEASED, then=Value(2)),
    output_field=IntegerField(),
)


def building_list_queryset():
    """All buildings with the extra values the list needs for filters and sorting.

    planned_date      earliest planned reading (from the tours)
    installation_date earliest planned installation of one of its orders
    effective_minutes reading time (manual beats calculated)
    """
    reading_stops = TourStop.objects.filter(building=OuterRef("pk"), kind=StopKind.READING).order_by("tour__date")
    installation_stops = TourStop.objects.filter(
        installation_order__building=OuterRef("pk"), kind=StopKind.INSTALLATION
    ).order_by("tour__date")
    return (
        Building.objects.select_related("property_manager", "assigned_reader", "cost_documents")
        .annotate(
            planned_date=Subquery(reading_stops.values("tour__date")[:1]),
            installation_date=Subquery(installation_stops.values("tour__date")[:1]),
            has_orders=Exists(InstallationOrder.objects.filter(building=OuterRef("pk"))),
            effective_minutes=Coalesce("reading_minutes_manual", "reading_minutes_calculated"),
            status_rank=STATUS_RANK,
        )
    )


def with_schedule_details(buildings):
    """Load the stops and orders needed for the "Ablesung und Montage" column.

    Only used for the rows actually shown (one page), so it stays fast.
    """
    stops_with_person = TourStop.objects.select_related("tour__employee").order_by("tour__date", "tour__start_time")
    return buildings.prefetch_related(
        Prefetch(
            "tour_stops",
            queryset=stops_with_person.filter(kind=StopKind.READING),
            to_attr="reading_stops",
        ),
        Prefetch(
            "installation_orders",
            queryset=InstallationOrder.objects.order_by("re_number").prefetch_related(
                Prefetch("tour_stops", queryset=stops_with_person.filter(kind=StopKind.INSTALLATION))
            ),
            to_attr="list_orders",
        ),
    )


def building_summary(buildings):
    """Numbers for the KPI tiles (like renderKpis in the prototype)."""
    numbers = buildings.aggregate(
        total=Count("pk"),
        released=Count("pk", filter=Q(status=BuildingStatus.RELEASED)),
        open=Count("pk", filter=Q(status=BuildingStatus.OPEN)),
        rework=Count("pk", filter=Q(status=BuildingStatus.REWORK)),
        hkv=Coalesce(Sum("hkv_count"), 0),
        apartments=Coalesce(Sum("apartments"), 0),
        gateways=Count("pk", filter=Q(has_gateway=True)),
    )
    numbers["with_orders"] = buildings.filter(has_orders=True).count()
    total = numbers["total"] or 1
    for key in ("released", "open", "rework"):
        numbers[f"{key}_pct"] = round(numbers[key] / total * 100)
    return numbers


def source_summary():
    """Header line: 'Stichtag 31.12.2026 · 240 Liegenschaften · 209 BFW / 31 CEOS'."""
    counts = dict(Building.objects.values_list("source_system").annotate(n=Count("pk")))
    return {
        "stichtage": list(Building.objects.order_by("stichtag").values_list("stichtag", flat=True).distinct()),
        "total": sum(counts.values()),
        "bfw": counts.get(SourceSystem.BFW_MAIN, 0),
        "ceos": counts.get(SourceSystem.CEOS, 0),
        "miclas": counts.get(SourceSystem.MICLAS, 0),
    }
