"""
Buildings (Liegenschaften), property managers and installation orders.

Data origin:
- Building: BFW Main / CEOS (in the prototype: const DATA)
- InstallationOrder: Miclas (in the prototype: const EMBEDDED_MONTAGE,
  line items from the Montage dashboard). Prices are deliberately NOT stored.
"""

from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords

from core.models import GeocodedAddress, TimeStampedModel

from .rules.file_numbers import normalize_file_number


class SourceSystem(models.TextChoices):
    """Where a record comes from. Real integrations can be added later."""

    BFW_MAIN = "bfw_main", "BFW Main"
    CEOS = "ceos", "CEOS"
    MICLAS = "miclas", "Miclas"


class BuildingStatus(models.TextChoices):
    OPEN = "open", "offen"
    REWORK = "rework", "Nacharbeit"
    RELEASED = "released", "freigegeben"


class InstallationType(models.TextChoices):
    """'Anlagenstatus' from the prototype (const ANLAGE)."""

    RADIO = "radio", "Funkanlage"
    RADIO_GATEWAY = "radio_gateway", "Funkanlage + Gateway"
    PARTIAL_RADIO = "partial_radio", "Teilfunkanlage"
    SONTEX_MANUAL = "sontex_manual", "Sontex 566 + alles andere manuell"
    MANUAL = "manual", "Manuell ablesen"


class PropertyManager(TimeStampedModel):
    """Hausverwaltung. A separate table so the filter has a clean list."""

    name = models.CharField("Name", max_length=200, unique=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]
        verbose_name = "Hausverwaltung"
        verbose_name_plural = "Hausverwaltungen"

    def __str__(self):
        return self.name


class Building(TimeStampedModel, GeocodedAddress):
    """A residential building whose meters we read (Liegenschaft)."""

    # --- identity -------------------------------------------------------------
    source_system = models.CharField("Quellsystem", max_length=20, choices=SourceSystem.choices)
    file_number = models.CharField("Liegenschafts-Nr. (AZ)", max_length=20)
    # Filled automatically in save(); used to match BFW/CEOS numbers and orders.
    file_number_core = models.CharField("AZ-Kern", max_length=20, db_index=True, editable=False)

    # --- billing period -------------------------------------------------------
    stichtag = models.DateField("Stichtag", db_index=True)
    billing_period_start = models.DateField("Abrechnungszeitraum von", null=True, blank=True)
    billing_period_end = models.DateField("Abrechnungszeitraum bis", null=True, blank=True)

    # --- planning -------------------------------------------------------------
    region = models.CharField("Region", max_length=100, blank=True, db_index=True)
    status = models.CharField(
        "Status", max_length=10, choices=BuildingStatus.choices, default=BuildingStatus.OPEN, db_index=True
    )
    status_changed_at = models.DateTimeField("Status geändert am", null=True, blank=True)
    # Readers may only *propose* a status (role table). Office staff decide.
    proposed_status = models.CharField(
        "vorgeschlagener Status", max_length=10, choices=BuildingStatus.choices, blank=True
    )
    proposed_status_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="vorgeschlagen von", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    proposed_status_at = models.DateTimeField("vorgeschlagen am", null=True, blank=True)
    # Responsible reader. The actual planned day lives only on planning.TourStop.
    assigned_reader = models.ForeignKey(
        "planning.Employee", verbose_name="Ableser", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_buildings",
    )
    property_manager = models.ForeignKey(
        PropertyManager, verbose_name="Hausverwaltung", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="buildings",
    )

    # --- technical data -------------------------------------------------------
    reading_type = models.CharField("Ableseart", max_length=100, blank=True, db_index=True)
    installation_type = models.CharField(
        "Anlagenstatus", max_length=20, choices=InstallationType.choices, blank=True
    )
    hkv_type = models.CharField("HKV-Typ", max_length=100, blank=True)
    hkv_family = models.CharField("HKV-Familie", max_length=50, blank=True)
    hkv_variant = models.CharField("HKV-Variante", max_length=50, blank=True)
    has_gateway = models.BooleanField("Gateway vorhanden", default=False)

    # Device counts (needed for reading time and filters)
    apartments = models.PositiveIntegerField("Wohnungen", default=0)
    hkv_count = models.PositiveIntegerField("HKV", default=0)
    wmz_count = models.PositiveIntegerField("WMZ", default=0)
    wwz_count = models.PositiveIntegerField("WWZ", default=0)
    kwz_count = models.PositiveIntegerField("KWZ", default=0)
    rwm_count = models.PositiveIntegerField("RWM Anzahl", default=0)
    sz_count = models.PositiveIntegerField("SZ", default=0)
    hwmz_count = models.PositiveIntegerField("HWMZ", default=0)
    # "Ja" / "Nein" / unknown in the source data -> True / False / None
    has_rwm = models.BooleanField("RWM", null=True, blank=True)
    has_gwz = models.BooleanField("GWZ", null=True, blank=True)
    has_hwz = models.BooleanField("HWZ", null=True, blank=True)
    has_wwmz = models.BooleanField("WWMZ", null=True, blank=True)
    detail_heat = models.CharField("Details Wärme", max_length=200, blank=True)
    detail_cold_water = models.CharField("Details KWZ", max_length=200, blank=True)

    # --- notes (may contain tenant data -> only visible by role) ---------------
    remark = models.TextField("Bemerkung (Quellsystem)", blank=True)
    note = models.TextField("Notiz", blank=True)
    handwritten_note = models.CharField("Handschriftlicher Vermerk", max_length=200, blank=True)
    order_reference = models.CharField("Auftrag (RE-Nr.)", max_length=100, blank=True)

    # --- reading time -----------------------------------------------------------
    reading_minutes_calculated = models.PositiveIntegerField("Ablesezeit berechnet (min)", default=0)
    reading_minutes_manual = models.PositiveIntegerField("Ablesezeit manuell (min)", null=True, blank=True)

    # --- apartment access (result of buildings/rules/access.py) ----------------
    access_apartment = models.BooleanField("Zugang zur Wohnung nötig", default=False)
    access_room = models.BooleanField("Zugang Heiz-/Technikraum nötig", default=False)
    access_units = models.JSONField("betroffene Nutzeinheiten", default=list, blank=True)
    access_reasons = models.JSONField("Gründe", default=list, blank=True)
    key_hint = models.CharField("Hinweis Schlüssel", max_length=200, blank=True)
    announcement_hint = models.CharField("Hinweis Anmeldung", max_length=200, blank=True)

    # Original record from the import, so nothing gets lost.
    raw_data = models.JSONField("Rohdaten", default=dict, blank=True)

    history = HistoricalRecords(excluded_fields=["raw_data"])

    class Meta:
        ordering = ["file_number"]
        verbose_name = "Liegenschaft"
        verbose_name_plural = "Liegenschaften"
        constraints = [
            models.UniqueConstraint(
                fields=["source_system", "file_number"], name="unique_building_per_source"
            ),
        ]
        permissions = [
            ("assign_employee", "Ableser/Monteur zuweisen"),
            ("set_status_open_rework", "Status offen/Nacharbeit setzen"),
            ("release_building", "Status freigegeben setzen"),
            ("propose_status", "Statusänderung vorschlagen"),
        ]

    def __str__(self):
        return f"{self.file_number} {self.street}, {self.city}"

    def save(self, *args, **kwargs):
        self.file_number_core = normalize_file_number(self.file_number)
        super().save(*args, **kwargs)

    @property
    def reading_minutes(self):
        """Effective reading time: a manual value beats the calculated one."""
        return self.reading_minutes_manual or self.reading_minutes_calculated


class DeviceCategory(models.Model):
    """Installation time per device category (editable in the admin).

    From CATS in the Montage dashboard, e.g. EHKV = 8 min per piece.
    """

    code = models.CharField("Kürzel", max_length=20, unique=True)
    label = models.CharField("Bezeichnung", max_length=100)
    minutes_per_piece = models.PositiveIntegerField("Minuten pro Stück", default=0)

    class Meta:
        ordering = ["code"]
        verbose_name = "Gerätekategorie"
        verbose_name_plural = "Gerätekategorien"

    def __str__(self):
        return self.label


class OrderStatus(models.TextChoices):
    OPEN = "open", "Offen"
    PLANNED = "planned", "Verplant"
    WORK_CARD = "work_card", "Arbeitskarte"
    IN_PROGRESS = "in_progress", "In Bearbeitung"
    DONE = "done", "Erledigt"


class OrderPriority(models.TextChoices):
    PRIO_1 = "prio_1", "Prio 1"
    PRIO_2 = "prio_2", "Prio 2"
    PRIO_3 = "prio_3", "Prio 3"
    WITH_READING = "with_reading", "Zusammen mit der HA"


class ContractType(models.TextChoices):
    RENT = "rent", "Miete"
    PURCHASE = "purchase", "Kauf"


class WorkType(models.TextChoices):
    INSTALLATION = "installation", "Montage"
    EXCHANGE = "exchange", "Tausch"


class InstallationOrder(TimeStampedModel, GeocodedAddress):
    """An installation order from Miclas (RE number).

    The link to a building is optional: in the demo data only 28 of 70
    orders can be matched (via RE number or building number). That is why
    an order has its own address and coordinates.
    """

    source_system = models.CharField(
        "Quellsystem", max_length=20, choices=SourceSystem.choices, default=SourceSystem.MICLAS
    )
    re_number = models.CharField("RE-Nr.", max_length=20, unique=True)
    process_number = models.CharField("Vorgang", max_length=20, blank=True)
    building = models.ForeignKey(
        Building, verbose_name="Liegenschaft", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="installation_orders",
    )
    building_file_number = models.CharField("Liegenschafts-Nr. laut Auftrag", max_length=20, blank=True)
    building_file_number_core = models.CharField("AZ-Kern", max_length=20, blank=True, db_index=True, editable=False)
    client = models.CharField("Auftraggeber", max_length=200, blank=True)
    order_date = models.DateField("Auftragsdatum", null=True, blank=True)
    status = models.CharField("Status", max_length=20, choices=OrderStatus.choices, default=OrderStatus.OPEN, db_index=True)
    priority = models.CharField("Priorität", max_length=20, choices=OrderPriority.choices, blank=True)
    contract_type = models.CharField("Miete/Kauf", max_length=20, choices=ContractType.choices, blank=True)
    work_type = models.CharField("Montage/Tausch", max_length=20, choices=WorkType.choices, blank=True)
    # Responsible installers (max. 3, checked in the form). The planned day
    # lives only on planning.TourStop.
    assigned_installers = models.ManyToManyField(
        "planning.Employee", verbose_name="Monteure", blank=True, related_name="assigned_orders"
    )
    duration_minutes_calculated = models.PositiveIntegerField("Montagezeit berechnet (min)", default=0)
    duration_minutes_manual = models.PositiveIntegerField("Montagezeit manuell (min)", null=True, blank=True)
    summary = models.CharField("Kurzbeschreibung", max_length=300, blank=True)
    raw_data = models.JSONField("Rohdaten", default=dict, blank=True)

    history = HistoricalRecords(excluded_fields=["raw_data"])

    class Meta:
        ordering = ["re_number"]
        verbose_name = "Montageauftrag"
        verbose_name_plural = "Montageaufträge"

    def __str__(self):
        return f"{self.re_number} {self.street}, {self.city}"

    def save(self, *args, **kwargs):
        self.building_file_number_core = normalize_file_number(self.building_file_number)
        super().save(*args, **kwargs)

    @property
    def duration_minutes(self):
        return self.duration_minutes_manual or self.duration_minutes_calculated


class InstallationOrderItem(models.Model):
    """One contract line of an order: article, description, quantity. No prices."""

    order = models.ForeignKey(InstallationOrder, on_delete=models.CASCADE, related_name="items")
    article_number = models.CharField("Artikel-Nr.", max_length=30)
    description = models.CharField("Bezeichnung", max_length=200)
    quantity = models.PositiveIntegerField("Menge", default=1)
    category = models.ForeignKey(
        DeviceCategory, verbose_name="Kategorie", null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        verbose_name = "Auftragsposition"
        verbose_name_plural = "Auftragspositionen"

    def __str__(self):
        return f"{self.quantity}× {self.description}"
