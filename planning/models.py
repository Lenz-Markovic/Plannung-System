"""
People, absences and day plans (tours).

A Tour is one person's plan for one day. Its TourStops are the buildings
(reading) or installation orders (installation) in driving order. The planned
day and person of a building/order are stored ONLY here, never a second time
on the building, so the two can never disagree.
"""

import datetime

from django.conf import settings
from django.db import models
from django.db.models import Q
from simple_history.models import HistoricalRecords

from core.models import GeocodedAddress, TimeStampedModel

from .rules.working_time import MAX_NET_MINUTES, MIN_NET_MINUTES


class Employee(TimeStampedModel, GeocodedAddress):
    """Extra data for a user who reads and/or installs.

    The address fields are the home address: routes start and end there,
    but the drive from/to home does not count as working time.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="employee")
    short_name = models.CharField("Kurzname", max_length=50, unique=True)
    can_read = models.BooleanField("Ableser", default=True)
    can_install = models.BooleanField("Monteur", default=False)
    calendar_color = models.CharField("Kalenderfarbe", max_length=7, default="#0d6efd")
    max_daily_minutes = models.PositiveSmallIntegerField("max. Netto-Arbeitszeit/Tag (min)", default=450)
    default_start_time = models.TimeField("übliche Startzeit", default=datetime.time(8, 0))
    # Personal time window, e.g. 09:15-15:00 (spec section 8). Empty = none.
    work_window_start = models.TimeField("Zeitfenster von", null=True, blank=True)
    work_window_end = models.TimeField("Zeitfenster bis", null=True, blank=True)
    active = models.BooleanField("aktiv", default=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["short_name"]
        verbose_name = "Mitarbeiter"
        verbose_name_plural = "Mitarbeiter"

    def __str__(self):
        return self.short_name


class AbsenceKind(models.TextChoices):
    VACATION = "vacation", "Urlaub"
    SICK = "sick", "Krank"
    TRAINING = "training", "Schulung"
    OTHER = "other", "Sonstiges"


class Absence(TimeStampedModel):
    """Days on which a person must not be planned (vacation, sick, ...)."""

    employee = models.ForeignKey(Employee, verbose_name="Mitarbeiter", on_delete=models.CASCADE, related_name="absences")
    kind = models.CharField("Art", max_length=20, choices=AbsenceKind.choices, default=AbsenceKind.VACATION)
    start_date = models.DateField("von")
    end_date = models.DateField("bis")
    note = models.CharField("Notiz", max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["start_date"]
        verbose_name = "Abwesenheit"
        verbose_name_plural = "Abwesenheiten"
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=models.F("start_date")), name="absence_end_after_start"),
        ]

    def __str__(self):
        return f"{self.employee}: {self.get_kind_display()} {self.start_date:%d.%m.}–{self.end_date:%d.%m.%Y}"


class TourStatus(models.TextChoices):
    PROVISIONAL = "provisional", "vorläufig"
    CONFIRMED = "confirmed", "bestätigt"
    DONE = "done", "erledigt"


class RoutingSource(models.TextChoices):
    NONE = "none", "keine echten Fahrzeiten"
    TOMTOM = "tomtom", "TomTom"


class Tour(TimeStampedModel):
    """One person's plan for one day (Fahrplan)."""

    employee = models.ForeignKey(Employee, verbose_name="Mitarbeiter", on_delete=models.PROTECT, related_name="tours")
    date = models.DateField("Datum", db_index=True)
    start_time = models.TimeField("Beginn", default=datetime.time(8, 0))
    status = models.CharField("Status", max_length=20, choices=TourStatus.choices, default=TourStatus.PROVISIONAL)

    # Optimistic locking: every save increases the version. A save based on
    # an older version is rejected ("Plan wurde zwischenzeitlich geändert").
    version = models.PositiveIntegerField("Version", default=1)

    # Set when a stop was moved out of this tour: it must be recalculated.
    needs_recalculation = models.BooleanField("neu rechnen", default=False)
    change_reason = models.CharField("Änderungsgrund", max_length=300, blank=True)

    routing_source = models.CharField(
        "Fahrzeiten", max_length=10, choices=RoutingSource.choices, default=RoutingSource.NONE
    )
    routing_note = models.CharField("Quelle der Fahrzeiten", max_length=300, blank=True)
    routing_calculated_at = models.DateTimeField("Fahrzeiten gerechnet am", null=True, blank=True)

    # Results of the working-time calculation (planning/rules/working_time.py)
    work_minutes = models.PositiveIntegerField("Ablese-/Montagezeit (min)", default=0)
    drive_minutes = models.PositiveIntegerField("Fahrzeit (min)", default=0)
    distance_km = models.DecimalField("Strecke (km)", max_digits=7, decimal_places=1, default=0)
    break_minutes = models.PositiveIntegerField("Pause (min)", default=0)
    break_after_position = models.PositiveSmallIntegerField("Pause nach Stopp", null=True, blank=True)
    end_time = models.TimeField("Ende", null=True, blank=True)
    # Drive from home / back home: shown, but NOT working time.
    commute_to_minutes = models.PositiveIntegerField("Anfahrt von zu Hause (min)", null=True, blank=True)
    commute_to_km = models.DecimalField("Anfahrt (km)", max_digits=6, decimal_places=1, null=True, blank=True)
    commute_from_minutes = models.PositiveIntegerField("Heimfahrt (min)", null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="bestätigt von", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    confirmed_at = models.DateTimeField("bestätigt am", null=True, blank=True)

    # Working time over 7,5 h or under 6 h, approved knowingly by the planner
    time_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Arbeitszeit übernommen von", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )
    time_approved_at = models.DateTimeField("Arbeitszeit übernommen am", null=True, blank=True)
    time_approval_note = models.CharField("Begründung Arbeitszeit", max_length=300, blank=True)

    # Big objects: more people work the same plan together (drive together, same stops)
    team = models.ManyToManyField(Employee, verbose_name="im Team mit", blank=True, related_name="team_tours")
    split_work = models.BooleanField("Arbeitszeit auf das Team aufteilen", default=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["date", "employee__short_name"]
        verbose_name = "Fahrplan"
        verbose_name_plural = "Fahrpläne"
        constraints = [
            models.UniqueConstraint(fields=["employee", "date"], name="one_tour_per_employee_and_day"),
        ]
        permissions = [
            ("confirm_tour", "Fahrplan bestätigen"),
            ("view_own_tours", "Eigenen Tagesplan sehen"),
            ("view_reports", "Auswertungen und Zeiten je Ableser sehen"),
            ("view_week_hours", "Wochenstunden je Mitarbeiter sehen"),  # only Admin by default
        ]

    def __str__(self):
        return f"{self.employee} {self.date:%d.%m.%Y}"

    @property
    def net_minutes(self):
        """Net working time = reading/installation + driving between stops."""
        return self.work_minutes + self.drive_minutes

    @property
    def people(self):
        """Lead + team, e.g. [Keller, Kaiser] (the team is prefetched where it is used a lot)."""
        return [self.employee, *sorted(self.team.all(), key=lambda e: e.short_name)]

    @property
    def people_label(self):
        """'Keller' or 'Keller + Kaiser'."""
        return " + ".join(e.short_name for e in self.people)

    @property
    def time_state(self):
        """'over' (more than 7,5 h), 'under' (less than 6 h) or '' - see rules/working_time.py."""
        if self.net_minutes > MAX_NET_MINUTES:
            return "over"
        return "under" if 0 < self.net_minutes < MIN_NET_MINUTES else ""

    @property
    def time_open(self):
        """Working time outside the range and nobody approved it yet."""
        return bool(self.time_state) and not self.time_approved_at


class StopKind(models.TextChoices):
    READING = "reading", "Ablesung"
    INSTALLATION = "installation", "Montage"


class DriveSource(models.TextChoices):
    NONE = "none", "keine"
    ESTIMATE = "estimate", "Schätzung"
    TOMTOM = "tomtom", "TomTom"


class TourStop(TimeStampedModel):
    """One stop in a tour: a reading (building) or an installation (order)."""

    tour = models.ForeignKey(Tour, on_delete=models.CASCADE, related_name="stops")
    position = models.PositiveSmallIntegerField("Reihenfolge")
    kind = models.CharField("Art", max_length=20, choices=StopKind.choices, default=StopKind.READING)
    building = models.ForeignKey(
        "buildings.Building", verbose_name="Liegenschaft", null=True, blank=True,
        on_delete=models.PROTECT, related_name="tour_stops",
    )
    installation_order = models.ForeignKey(
        "buildings.InstallationOrder", verbose_name="Montageauftrag", null=True, blank=True,
        on_delete=models.PROTECT, related_name="tour_stops",
    )
    # Fixed appointment: automatic re-ordering never moves this stop.
    is_fixed = models.BooleanField("Fixtermin", default=False)

    start_time = models.TimeField("von", null=True, blank=True)
    end_time = models.TimeField("bis", null=True, blank=True)
    work_minutes = models.PositiveIntegerField("Dauer (min)", default=0)

    # Drive from THIS stop to the NEXT one (as in the prototype).
    drive_to_next_seconds = models.PositiveIntegerField("Fahrt exakt (s)", null=True, blank=True)
    drive_to_next_minutes = models.PositiveIntegerField("Fahrt im Plan (min)", null=True, blank=True)
    drive_to_next_km = models.DecimalField("Fahrt (km)", max_digits=6, decimal_places=1, null=True, blank=True)
    departure_time = models.TimeField("Abfahrt", null=True, blank=True)
    drive_source = models.CharField("Fahrzeit-Quelle", max_length=10, choices=DriveSource.choices, default=DriveSource.NONE)
    # Roadworks / closures / traffic jams reported by TomTom for the next leg.
    route_warnings = models.JSONField("Hinweise zur Strecke", default=list, blank=True)

    field_note = models.TextField("Notiz vor Ort", blank=True)
    done_at = models.DateTimeField("erledigt am", null=True, blank=True)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="erledigt von", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="+",
    )

    history = HistoricalRecords()

    class Meta:
        ordering = ["tour", "position"]
        verbose_name = "Stopp"
        verbose_name_plural = "Stopps"
        constraints = [
            # A reading needs a building; an installation needs an order.
            models.CheckConstraint(
                condition=(
                    Q(kind="reading", building__isnull=False, installation_order__isnull=True)
                    | Q(kind="installation", installation_order__isnull=False)
                ),
                name="stop_kind_matches_reference",
            ),
        ]
        permissions = [
            ("mark_stop_done", "Stopp als erledigt markieren"),
            ("add_field_note", "Notiz vor Ort erfassen"),
        ]

    def __str__(self):
        target = self.building or self.installation_order
        return f"{self.tour} #{self.position}: {target}"
