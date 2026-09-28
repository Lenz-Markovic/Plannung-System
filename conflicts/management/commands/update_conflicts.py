"""
Recalculate all stored conflicts (reading vs. installation):

    python manage.py update_conflicts

Normally not needed - the check runs after every planning change. Use it
once after an update of the program, or if the list looks out of date.
"""

from django.core.management.base import BaseCommand

from conflicts.models import Conflict
from conflicts.services import refresh_conflicts


class Command(BaseCommand):
    help = "Prüft alle Liegenschaften neu auf Konflikte zwischen Montage und Ablesung."

    def handle(self, *args, **options):
        count = refresh_conflicts()
        critical = Conflict.objects.filter(severity="critical", acknowledged_at__isnull=True).count()
        self.stdout.write(self.style.SUCCESS(f"{count} Meldungen gespeichert, davon {critical} offene Konflikte."))
