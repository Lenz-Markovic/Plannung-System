"""
Montageaufträge list (like the Montage dashboard of the prototype):
query, filters and the inline changes of one order.

Prices are only used by the Admin budget view "💶 Material & Kosten" (material.py).
"""

import django_filters
from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Exists, F, Min, OuterRef, Prefetch, Q

from journal.activity import record
from journal.models import ActivityKind
from journal.notes import note_annotations
from conflicts.models import Conflict, Severity
from conflicts.services import refresh_for
from planning.models import Employee, StopKind, TourStop

from .models import InstallationOrder, OrderPriority, OrderStatus

MAX_INSTALLERS = 3  # MAX_MONTEURE in the prototype
NONE = "__none"

SORTS = {
    "termin": (F("installation_date").asc(nulls_last=True), "re_number"),
    "re": ("re_number",),
    "datum": (F("order_date").desc(nulls_last=True), "re_number"),
    "ort": ("city", "street"),
}
OPEN_CONFLICT = Q(acknowledged_at__isnull=True, severity__in=[Severity.CRITICAL, Severity.WARNING])


def order_list_queryset():
    """All orders with what one row needs (few queries, whatever the number of rows)."""
    stops = (TourStop.objects.filter(kind=StopKind.INSTALLATION).select_related("tour__employee")
             .order_by("tour__date", "start_time"))
    return (
        InstallationOrder.objects.select_related("building")
        .annotate(
            installation_date=Min("tour_stops__tour__date", filter=Q(tour_stops__kind=StopKind.INSTALLATION)),
            has_open_conflict=Exists(Conflict.objects.filter(OPEN_CONFLICT, installation_order=OuterRef("pk"))),
            **note_annotations("installation_order"),  # 📝 / ⛔ badges in the row
        )
        .prefetch_related(
            Prefetch("tour_stops", queryset=stops, to_attr="planned"),
            Prefetch("conflicts", queryset=Conflict.objects.order_by("severity"), to_attr="all_conflicts"),
            "assigned_installers", "items__category",
        )
    )


def date_input():
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class OrderFilter(django_filters.FilterSet):
    """Same filters as passesFilters() in the prototype, plus appointment and conflicts."""

    q = django_filters.CharFilter(
        label="Suche", method="filter_search",
        widget=forms.TextInput(attrs={"type": "search", "placeholder": "Liegenschaft, RE, Adresse, Auftraggeber"}))
    priority = django_filters.ChoiceFilter(
        label="Priorität", empty_label="alle", method="filter_priority",
        choices=[(NONE, "(keine)"), *OrderPriority.choices])
    installer = django_filters.ChoiceFilter(label="Monteur", empty_label="alle", method="filter_installer")
    status = django_filters.ChoiceFilter(label="Status", empty_label="alle", choices=OrderStatus.choices)
    termin = django_filters.ChoiceFilter(
        label="Termin", empty_label="alle", method="filter_termin",
        choices=[("mit", "mit Montagetermin"), ("ohne", "ohne Montagetermin")])
    konflikt = django_filters.ChoiceFilter(
        label="Konflikte", empty_label="alle", method="filter_konflikt",
        choices=[("offen", "nur mit offenem Konflikt"), ("keine", "ohne offenen Konflikt")])
    von = django_filters.DateFilter(label="Auftrag von", field_name="order_date", lookup_expr="gte", widget=date_input())
    bis = django_filters.DateFilter(label="bis", field_name="order_date", lookup_expr="lte", widget=date_input())
    sort = django_filters.ChoiceFilter(
        label="Sortieren", empty_label=None, method="filter_sort",
        choices=[("termin", "nach Montagetermin"), ("re", "nach RE-Nr."), ("datum", "neueste Aufträge zuerst"),
                 ("ort", "nach Ort")])

    class Meta:
        model = InstallationOrder
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters["installer"].extra["choices"] = [(NONE, "(nicht zugewiesen)")] + [
            (str(e.pk), e.short_name) for e in Employee.objects.filter(can_install=True, active=True)]

    @property
    def qs(self):
        queryset = super().qs
        if (self.data or {}).get("sort") not in SORTS:  # no (valid) sort chosen: by appointment
            queryset = queryset.order_by(*SORTS["termin"])
        return queryset

    def filter_search(self, queryset, name, value):
        words = value.split()
        for word in words:
            queryset = queryset.filter(
                Q(re_number__icontains=word) | Q(process_number__icontains=word) | Q(building_file_number__icontains=word)
                | Q(street__icontains=word) | Q(zip_code__icontains=word) | Q(city__icontains=word)
                | Q(client__icontains=word) | Q(summary__icontains=word))
        return queryset

    def filter_priority(self, queryset, name, value):
        return queryset.filter(priority="") if value == NONE else queryset.filter(priority=value)

    def filter_installer(self, queryset, name, value):
        if value == NONE:
            return queryset.filter(assigned_installers__isnull=True)
        return queryset.filter(assigned_installers=value).distinct()

    def filter_termin(self, queryset, name, value):
        return queryset.filter(installation_date__isnull=(value == "ohne"))

    def filter_konflikt(self, queryset, name, value):
        return queryset.filter(has_open_conflict=(value == "offen"))

    def filter_sort(self, queryset, name, value):
        return queryset.order_by(*SORTS.get(value, SORTS["termin"]))


# --- changes from the table -------------------------------------------------------------

def update_order(order, data, user):
    """Save the ONE field sent from a row: priority, status, duration or installers."""
    if not user.has_perm("buildings.change_installationorder"):
        raise PermissionDenied("Montageaufträge ändern darf deine Rolle nicht.")
    if "priority" in data:
        if data["priority"] not in ["", *OrderPriority.values]:
            raise ValidationError("Unbekannte Priorität.")
        order.priority = data["priority"]
        field = "Priorität"
    elif "status" in data:
        if data["status"] not in OrderStatus.values:
            raise ValidationError("Unbekannter Status.")
        order.status = data["status"]
        field = "Status"
    elif "duration" in data:
        value = data["duration"].strip()
        if value and (not value.isdigit() or not 0 < int(value) <= 2000):
            raise ValidationError("Montagezeit bitte in Minuten (1–2000).")
        # the calculated value again when emptied or set to the calculated minutes
        order.duration_minutes_manual = int(value) if value and int(value) != order.duration_minutes_calculated else None
        field = "Montagezeit"
    elif "installers_sent" in data:
        ids = data.getlist("installers")
        if len(ids) > MAX_INSTALLERS:
            raise ValidationError(f"Höchstens {MAX_INSTALLERS} Monteure pro Auftrag.")
        order.save()
        order.assigned_installers.set(Employee.objects.filter(pk__in=ids, can_install=True))
        refresh_for(order_ids=[order.pk])
        names = ", ".join(e.short_name for e in order.assigned_installers.all()) or "keine"
        record(user, ActivityKind.ORDER, f"{order.re_number} {order.street}: Monteure → {names}", order=order)
        return "Monteure"
    else:
        raise ValidationError("Kein Feld angegeben.")
    order.save()
    refresh_for(order_ids=[order.pk])  # e.g. "Erledigt" -> the conflict is solved
    value = {"Priorität": order.get_priority_display() or "keine", "Status": order.get_status_display(),
             "Montagezeit": f"{order.duration_minutes} min"}[field]
    record(user, ActivityKind.ORDER, f"{order.re_number} {order.street}: {field} → {value}", order=order)
    return field
