"""
Recalculate the apartment access (🔑 / 🚪) of all buildings from their notes.

    python manage.py update_access

Only the access fields are changed; status, notes etc. stay as they are.
"""

from django.core.management.base import BaseCommand

from buildings.models import Building
from buildings.services import apply_access

ACCESS_FIELDS = ["access_apartment", "access_room", "access_units", "access_reasons", "key_hint", "announcement_hint"]


class Command(BaseCommand):
    help = "Berechnet den Wohnungs-/Heizraum-Zugang aller Liegenschaften neu."

    def handle(self, *args, **options):
        buildings = list(Building.objects.all())
        for building in buildings:
            apply_access(building)
        Building.objects.bulk_update(buildings, ACCESS_FIELDS)  # no history entries for this technical update
        apartment = sum(1 for b in buildings if b.access_apartment)
        room = sum(1 for b in buildings if b.access_room)
        self.stdout.write(self.style.SUCCESS(
            f"{len(buildings)} Liegenschaften geprüft: {apartment}× Zugang Wohnung, {room}× Heizraum/Keller."))
