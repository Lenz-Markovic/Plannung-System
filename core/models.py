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
        "Geocoding", max_length=10, choices=GeocodeStatus.choices, default=GeocodeStatus.PENDING
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
