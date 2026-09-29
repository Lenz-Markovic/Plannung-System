"""
Cover sheets (Deckblätter) and received cost documents (Unterlagen).

14-day rule (spec section 7, pure function in documents/rules.py later):
the building must be released within 14 days after the cost documents
arrived. The deadline restarts at every status change or new appointment.
deadline_start and the last_seen_* fields store what is needed to detect
such a change.
"""

from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords

from core.models import TimeStampedModel


class CoverSheet(TimeStampedModel):
    """Data read from the cover sheet PDF (OCR) of a building."""

    building = models.OneToOneField(
        "buildings.Building", verbose_name="Liegenschaft", on_delete=models.CASCADE, related_name="cover_sheet"
    )
    source_file = models.CharField("Quelldatei", max_length=200, blank=True)
    page = models.PositiveIntegerField("Seite", null=True, blank=True)
    source_label = models.CharField("Quelle", max_length=200, blank=True)
    owner_text = models.TextField("Eigentümer laut Deckblatt", blank=True)
    # Original value; the editable value is Building.property_manager.
    property_manager_text = models.CharField("Hausverwaltung laut Deckblatt", max_length=200, blank=True)

    class Meta:
        verbose_name = "Deckblatt"
        verbose_name_plural = "Deckblätter"

    def __str__(self):
        return f"Deckblatt {self.building.file_number}"


class DeadlineResetReason(models.TextChoices):
    STATUS = "status", "Statuswechsel"
    APPOINTMENT = "appointment", "neuer Termin"


class CostDocumentReceipt(TimeStampedModel):
    """When the cost documents of a building arrived (Unterlagen-Eingang)."""

    building = models.OneToOneField(
        "buildings.Building", verbose_name="Liegenschaft", on_delete=models.CASCADE, related_name="cost_documents"
    )
    received_on = models.DateField("Unterlagen erhalten am")
    # Start of the current 14-day deadline (= received_on or last reset).
    deadline_start = models.DateField("Frist läuft ab")
    reset_reason = models.CharField(
        "Frist neu gestartet wegen", max_length=20, choices=DeadlineResetReason.choices, blank=True
    )
    last_seen_status = models.CharField("Status bei letzter Prüfung", max_length=10, blank=True)
    last_seen_planned_date = models.DateField("Termin bei letzter Prüfung", null=True, blank=True)
    entered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="eingetragen von", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Unterlagen-Eingang"
        verbose_name_plural = "Unterlagen-Eingänge"

    def __str__(self):
        return f"{self.building.file_number}: {self.received_on:%d.%m.%Y}"


class NoticeSettings(models.Model):
    """Contact data printed on every tenant notice ("Aushang-Einstellungen" in Verwaltung). One row."""

    company = models.CharField("Firma", max_length=200, default="Ihr Messdienst (bitte in der Verwaltung eintragen)")
    phone = models.CharField("Telefon", max_length=60, blank=True, default="")
    email = models.CharField("E-Mail", max_length=120, blank=True, default="")
    office_hours = models.CharField("Erreichbar", max_length=120, blank=True, default="Mo–Fr 8:00–16:00 Uhr")
    change_until_days = models.PositiveSmallIntegerField(
        "Terminänderung bis … Tage vorher", default=3,
        help_text="Auf dem Aushang: „Passt der Termin nicht? Bitte bis … anrufen.“")
    extra_text = models.TextField("Zusätzlicher Hinweis", blank=True, default="",
                                  help_text="Erscheint unten auf jedem Aushang (z. B. Hinweis zur Heizung).")

    class Meta:
        verbose_name = "Aushang-Einstellungen"
        verbose_name_plural = "Aushang-Einstellungen"

    def __str__(self):
        return "Aushang-Einstellungen (Kontakt auf dem Aushang)"

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]
