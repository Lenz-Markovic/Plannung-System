"""
Stored conflict results.

The rules themselves are pure functions in conflicts/rules.py, the
database part is conflicts/services.py.
This table only holds the current result, so all users see the same
conflicts. After each plan, move or new order the open conflicts of the
affected buildings are replaced. No history: the rows are recalculated all
the time, a history would only be noise.
"""

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class ConflictRule(models.TextChoices):
    # Spec section 8 (reading vs. installation)
    INSTALL_AFTER_READING = "install_after_reading", "Montage nach der Ablesung"
    SAME_DAY = "same_day", "Montage und Ablesung am selben Tag"
    BUFFER_SHORT = "buffer_short", "Montage 1–7 Tage vor der Ablesung"
    RADIO_RETROFIT = "radio_retrofit", "Umrüstung auf Funk bei manueller Ableseart"
    SAME_PERSON = "same_person", "Monteur = Ableser, Termine ≤ 3 Tage auseinander"
    GATEWAY_NOT_MARKED = "gateway_not_marked", "Gateway-Montage, Liegenschaft nicht als Gateway markiert"
    ORDER_WITHOUT_DATE = "order_without_date", "Auftrag ohne Montagetermin"
    NO_READING_DATE = "no_reading_date", "Montage geplant, aber kein Ablesetag"
    # Spec section 6 (planning)
    ALREADY_IN_OTHER_TOUR = "already_in_other_tour", "Liegenschaft steht schon in einem anderen Plan"
    ALREADY_HAS_DATE = "already_has_date", "Liegenschaft hat schon einen Termin"
    # Absences
    EMPLOYEE_ABSENT = "employee_absent", "Mitarbeiter ist an diesem Tag abwesend"


class Severity(models.TextChoices):
    """Wording as in the spec. 'in Ordnung' is not stored."""

    INFO = "info", "Info"
    HINT = "hint", "Hinweis"
    WARNING = "warning", "Warnung"
    CRITICAL = "critical", "kritisch"


class Conflict(TimeStampedModel):
    rule = models.CharField("Regel", max_length=40, choices=ConflictRule.choices)
    severity = models.CharField("Bewertung", max_length=10, choices=Severity.choices, db_index=True)
    message = models.TextField("Meldung")

    building = models.ForeignKey(
        "buildings.Building", verbose_name="Liegenschaft", null=True, blank=True,
        on_delete=models.CASCADE, related_name="conflicts",
    )
    installation_order = models.ForeignKey(
        "buildings.InstallationOrder", verbose_name="Montageauftrag", null=True, blank=True,
        on_delete=models.CASCADE, related_name="conflicts",
    )
    stop = models.ForeignKey(
        "planning.TourStop", verbose_name="Stopp", null=True, blank=True,
        on_delete=models.CASCADE, related_name="conflicts",
    )
    other_stop = models.ForeignKey(
        "planning.TourStop", verbose_name="anderer Stopp", null=True, blank=True,
        on_delete=models.CASCADE, related_name="+",
    )

    # A dispatcher may knowingly save a plan despite a conflict: who and why.
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="bewusst übernommen von", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    acknowledged_at = models.DateTimeField("übernommen am", null=True, blank=True)
    acknowledged_note = models.CharField("Begründung", max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Konflikt"
        verbose_name_plural = "Konflikte"
        permissions = [
            ("acknowledge_conflict", "Konflikt bewusst übernehmen"),
        ]

    def __str__(self):
        return f"{self.get_severity_display()}: {self.get_rule_display()}"
