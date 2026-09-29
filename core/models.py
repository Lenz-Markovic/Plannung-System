"""
Shared building blocks for the models of all apps.

These are *abstract* models: Django does not create a table for them. Every
model that inherits from them simply gets their fields in its own table.
"""

from django.db import models


class TimeStampedModel(models.Model):
    """Adds created_at / updated_at to a model.

    updated_at is indexed because the HTMX polling asks
    "which rows changed since time X?".
    """

    created_at = models.DateTimeField("angelegt am", auto_now_add=True)
    updated_at = models.DateTimeField("geändert am", auto_now=True, db_index=True)

    class Meta:
        abstract = True


class GeocodeStatus(models.TextChoices):
    PENDING = "pending", "noch nicht gesucht"
    OK = "ok", "gefunden"
    APPROXIMATE = "approximate", "ungefähr (ohne Hausnummer)"
    FAILED = "failed", "nicht gefunden"


class GeocodedAddress(models.Model):
    """A postal address plus the coordinates TomTom found for it.

    Each address is geocoded only once. geocoded_address remembers which
    address text the coordinates belong to, so we only ask TomTom again
    when the address has changed.
    """

    street = models.CharField("Straße", max_length=200, blank=True)
    zip_code = models.CharField("PLZ", max_length=10, blank=True, db_index=True)
    city = models.CharField("Ort", max_length=100, blank=True)

    latitude = models.FloatField("Breitengrad", null=True, blank=True)
    longitude = models.FloatField("Längengrad", null=True, blank=True)
    geocode_status = models.CharField(
        "Geocoding", max_length=20, choices=GeocodeStatus.choices, default=GeocodeStatus.PENDING
    )
    geocoded_at = models.DateTimeField("geocodiert am", null=True, blank=True)
    geocoded_address = models.CharField("geocodierte Adresse", max_length=320, blank=True)

    class Meta:
        abstract = True

    @property
    def full_address(self):
        return f"{self.street}, {self.zip_code} {self.city}".strip(", ")

    @property
    def needs_geocoding(self):
        return self.geocode_status == GeocodeStatus.PENDING or self.geocoded_address != self.full_address


class Features(models.Model):
    """Switches for functions that are not released yet ("Funktionen" in Verwaltung).

    There is only ONE row (pk=1); Features.load() returns it. A new function can
    be built and tested, but stays invisible in the real system until an admin
    switches it on here.
    """

    autoplan = models.BooleanField(
        "🤖 Automatisch planen", default=False,
        help_text="Eingefroren – soll später weiterentwickelt werden. Eingeschaltet erscheint im Kalender der "
                  "Knopf „Automatisch planen“: das System verteilt ungeplante Stopps auf freie Tage (Vorschlag, "
                  "erst nach Bestätigung gespeichert).",
    )

    class Meta:
        verbose_name = "Funktionen"
        verbose_name_plural = "Funktionen"

    def __str__(self):
        return "Funktionen (ein-/ausschalten)"

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]


class RoleDefault(models.Model):
    """A default permission that was already given to a role once (core/roles.py).

    After `migrate`, only defaults that are NEW get added automatically - a permission an
    admin took away from a role on purpose stays away.
    """

    role = models.CharField("Rolle", max_length=80)
    permission = models.CharField("Recht", max_length=150)

    class Meta:
        unique_together = [("role", "permission")]
        verbose_name = "Standardrecht (vergeben)"
        verbose_name_plural = "Standardrechte (vergeben)"

    def __str__(self):
        return f"{self.role}: {self.permission}"
