"""
Filters and sorting of the building list (django-filter).

Every filter is one field in the form above the table, with the same
labels as in the Deckblätter prototype. The chosen values are in the URL
(?region=...&status=open), so a filtered list can be bookmarked or sent
to a colleague.
"""

import django_filters
from django import forms
from django.db.models import Exists, F, OuterRef, Q

from planning.models import Employee, StopKind, TourStop

from .models import Building, BuildingStatus, InstallationOrder, InstallationType, PropertyManager, SourceSystem

NO_PROPERTY_MANAGER = "__none"

# URL value -> database field. Anything else in ?sort= is ignored.
SORT_FIELDS = {
    "nr": "file_number",
    "adresse": "street",
    "region": "region",
    "ableseart": "reading_type",
    "anlage": "installation_type",
    "hkv": "hkv_count",
    "geraete": "wmz_count",
    "auftrag": "order_reference",
    "gateway": "has_gateway",
    "ablesung": "planned_date",
    "montage": "installation_date",
    "zeit": "effective_minutes",
    "status": "status_rank",
    "unterlagen": "cost_documents__received_on",
    "hausverwaltung": "property_manager__name",
}


def sort_buildings(queryset, sort_value):
    """Sort by a whitelisted column; empty values always at the end."""
    descending = sort_value.startswith("-")
    field = SORT_FIELDS.get(sort_value.lstrip("-"))
    if not field:
        return queryset.order_by("file_number")
    expression = F(field).desc(nulls_last=True) if descending else F(field).asc(nulls_last=True)
    return queryset.order_by(expression, "file_number")


def date_input():
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class BuildingFilter(django_filters.FilterSet):
    q = django_filters.CharFilter(
        label="Suche (Nr., Adresse, Auftrag)", method="filter_search",
        widget=forms.TextInput(attrs={"type": "search", "placeholder": "z. B. 0798615 oder Hauptstraße"}),
    )
    stichtag = django_filters.ChoiceFilter(label="Stichtag", empty_label="alle")
    region = django_filters.ChoiceFilter(label="Region", empty_label="alle Regionen")
    source_system = django_filters.ChoiceFilter(
        label="Deckblatt", empty_label="alle",
        choices=[(SourceSystem.BFW_MAIN.value, "BFW"), (SourceSystem.CEOS.value, "CEOS")],
    )
    hkv_family = django_filters.ChoiceFilter(label="HKV-Typ", empty_label="alle")
    hkv_variant = django_filters.ChoiceFilter(label="HKV-Variante", empty_label="alle")
    min_hkv = django_filters.NumberFilter(
        label="HKV mind.", field_name="hkv_count", lookup_expr="gte",
        widget=forms.NumberInput(attrs={"min": 0, "placeholder": "0"}),
    )
    gateway = django_filters.ChoiceFilter(
        label="Gateway", empty_label="alle", method="filter_gateway",
        choices=[("1", "nur Gateway-Anlagen"), ("0", "ohne Gateway")],
    )
    status = django_filters.ChoiceFilter(label="Status", empty_label="alle", choices=BuildingStatus.choices)
    installation_type = django_filters.ChoiceFilter(label="Anlage", empty_label="alle", choices=InstallationType.choices)
    reading_type = django_filters.ChoiceFilter(label="Ableseart", empty_label="alle")
    reader = django_filters.ModelChoiceFilter(
        label="Ableser", empty_label="alle", method="filter_reader",
        queryset=Employee.objects.filter(can_read=True, active=True),
    )
    montage = django_filters.ChoiceFilter(
        label="Montage", empty_label="alle", method="filter_montage",
        choices=[
            ("krit", "nur Termin-Konflikte"),
            ("termin", "nur mit Montagetermin"),
            ("kein", "Umrüstung ohne Termin"),
            ("any", "mit Montageauftrag"),
            ("none", "ohne Montageauftrag"),
        ],
    )
    property_manager = django_filters.ChoiceFilter(label="Hausverwaltung", empty_label="alle", method="filter_property_manager")
    documents = django_filters.ChoiceFilter(
        label="Unterlagen (Kosten)", empty_label="alle", method="filter_documents",
        choices=[("ja", "erhalten"), ("nein", "noch nicht erhalten")],
    )
    documents_from = django_filters.DateFilter(
        label="Unterlagen erhalten von", field_name="cost_documents__received_on", lookup_expr="gte", widget=date_input()
    )
    documents_to = django_filters.DateFilter(
        label="bis", field_name="cost_documents__received_on", lookup_expr="lte", widget=date_input()
    )
    only_orders = django_filters.BooleanFilter(
        label="nur mit Auftrag (RE)", method="filter_only_orders", widget=forms.CheckboxInput
    )

    class Meta:
        model = Building
        fields = []  # all filters are declared above

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Dropdown lists are filled from the data, so new values appear automatically.
        def values(field):
            existing = Building.objects.exclude(**{field: ""}).order_by(field).values_list(field, flat=True).distinct()
            return [(value, value) for value in existing]

        self.filters["region"].extra["choices"] = values("region")
        self.filters["hkv_family"].extra["choices"] = values("hkv_family")
        self.filters["hkv_variant"].extra["choices"] = values("hkv_variant")
        self.filters["reading_type"].extra["choices"] = values("reading_type")
        self.filters["stichtag"].extra["choices"] = [
            (d.isoformat(), d.strftime("%d.%m.%Y"))
            for d in Building.objects.order_by("stichtag").values_list("stichtag", flat=True).distinct()
        ]
        self.filters["property_manager"].extra["choices"] = [(NO_PROPERTY_MANAGER, "(ohne Hausverwaltung)")] + [
            (str(pk), name) for pk, name in PropertyManager.objects.values_list("pk", "name")
        ]

    # --- filter methods --------------------------------------------------------

    def filter_search(self, queryset, name, value):
        value = value.strip()
        if not value:
            return queryset
        order_match = InstallationOrder.objects.filter(building=OuterRef("pk"), re_number__icontains=value)
        return queryset.filter(
            Q(file_number__icontains=value)
            | Q(street__icontains=value)
            | Q(zip_code__startswith=value)
            | Q(city__icontains=value)
            | Q(order_reference__icontains=value)
            | Q(property_manager__name__icontains=value)
            | Exists(order_match)
        )

    def filter_gateway(self, queryset, name, value):
        return queryset.filter(has_gateway=(value == "1"))

    def filter_reader(self, queryset, name, employee):
        # Responsible reader OR planned in one of their tours
        planned = TourStop.objects.filter(building=OuterRef("pk"), kind=StopKind.READING, tour__employee=employee)
        return queryset.filter(Q(assigned_reader=employee) | Exists(planned))

    def filter_montage(self, queryset, name, value):
        if value == "none":
            return queryset.filter(has_orders=False)
        if value == "any":
            return queryset.filter(has_orders=True)
        if value == "termin":
            return queryset.filter(installation_date__isnull=False)
        if value == "kein":
            without_date = InstallationOrder.objects.filter(building=OuterRef("pk"), tour_stops__isnull=True)
            return queryset.filter(Exists(without_date))
        if value == "krit":
            # Spec section 8 "kritisch": installation on or after the reading day
            return queryset.filter(installation_date__isnull=False, planned_date__isnull=False,
                                   installation_date__gte=F("planned_date"))
        return queryset

    def filter_property_manager(self, queryset, name, value):
        if value == NO_PROPERTY_MANAGER:
            return queryset.filter(property_manager__isnull=True)
        return queryset.filter(property_manager_id=value)

    def filter_documents(self, queryset, name, value):
        return queryset.filter(cost_documents__isnull=(value == "nein"))

    def filter_only_orders(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(Q(has_orders=True) | ~Q(order_reference=""))
