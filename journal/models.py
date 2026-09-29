"""
📝 Notes on a building or an installation order, and 🕘 the Verlauf (who changed what).

Notes: written by the appointment team (Terminierung), e.g. "Storno" - the planners see
them in "Fahrplan prüfen" and in the calendar, and can add more on top. An open Storno
note keeps the object out of the suggestions until somebody marks it "erledigt".

Verlauf (Activity): one line per important action, written by the services
(journal/activity.py). The field history of simple_history stays as it is; this is the
readable list for people.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q


class NoteKind(models.TextChoices):
    INFO = "info", "Hinweis"
    STORNO = "storno", "Storno"
    WISH = "wish", "Terminwunsch"
    ACCESS = "access", "Zugang / Schlüssel"


NOTE_ICONS = {NoteKind.INFO: "📝", NoteKind.STORNO: "⛔", NoteKind.WISH: "📅", NoteKind.ACCESS: "🔑"}


class Note(models.Model):
    building = models.ForeignKey("buildings.Building", verbose_name="Liegenschaft", null=True, blank=True,
                                 on_delete=models.CASCADE, related_name="notes")
    installation_order = models.ForeignKey("buildings.InstallationOrder", verbose_name="Montageauftrag", null=True,
                                           blank=True, on_delete=models.CASCADE, related_name="notes")
    kind = models.CharField("Art", max_length=20, choices=NoteKind.choices, default=NoteKind.INFO)
    text = models.TextField("Notiz", max_length=2000)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="von", null=True, on_delete=models.SET_NULL,
                               related_name="+")
    created_at = models.DateTimeField("geschrieben am", auto_now_add=True)
    resolved_at = models.DateTimeField("erledigt am", null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="erledigt von", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Notiz"
        verbose_name_plural = "Notizen"
        constraints = [
            models.CheckConstraint(condition=Q(building__isnull=False) | Q(installation_order__isnull=False),
                                   name="note_has_target"),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.text[:40]}"

    @property
    def is_open(self):
        return self.resolved_at is None

    @property
    def icon(self):
        return NOTE_ICONS.get(self.kind, "📝")

    @property
    def target(self):
        return self.building or self.installation_order


class ActivityKind(models.TextChoices):
    PLAN = "plan", "Fahrplan"
    NOTICE = "notice", "Aushang"
    ORDER = "order", "Montageauftrag"
    STATUS = "status", "Status"
    NOTE = "note", "Notiz"
    DOCUMENTS = "documents", "Unterlagen"


ACTIVITY_ICONS = {ActivityKind.PLAN: "🗺", ActivityKind.NOTICE: "📄", ActivityKind.ORDER: "🔧",
                  ActivityKind.STATUS: "🏷", ActivityKind.NOTE: "📝", ActivityKind.DOCUMENTS: "📥"}


class Activity(models.Model):
    """One line of the Verlauf: who did what, when."""

    created_at = models.DateTimeField("wann", auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="wer", null=True, on_delete=models.SET_NULL,
                             related_name="+")
    kind = models.CharField("Bereich", max_length=20, choices=ActivityKind.choices, db_index=True)
    text = models.CharField("was", max_length=400)
    # what it was about (kept as text in `text` when the object is deleted later)
    tour = models.ForeignKey("planning.Tour", null=True, blank=True, on_delete=models.SET_NULL, related_name="activities")
    building = models.ForeignKey("buildings.Building", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="activities")
    installation_order = models.ForeignKey("buildings.InstallationOrder", null=True, blank=True,
                                           on_delete=models.SET_NULL, related_name="activities")

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "Verlauf-Eintrag"
        verbose_name_plural = "Verlauf"

    def __str__(self):
        return f"{self.created_at:%d.%m.%Y %H:%M} {self.user}: {self.text}"

    @property
    def icon(self):
        return ACTIVITY_ICONS.get(self.kind, "•")
