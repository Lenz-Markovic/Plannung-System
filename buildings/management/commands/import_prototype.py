"""
Import the demo data from the HTML prototypes into the database.

    python manage.py import_prototype
    python manage.py import_prototype --flush     # delete imported data first

By default the files in docs/prototype/ are used. Running the command again
updates the records instead of creating duplicates.
"""

import io
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from buildings.importers.prototype import PrototypeFormatError, import_prototype_data, read_prototypes
from buildings.models import Building, InstallationOrder, PropertyManager
from conflicts.models import Conflict
from conflicts.services import refresh_conflicts
from planning.models import Tour

DEFAULT_DIR = Path(settings.BASE_DIR) / "docs" / "prototype"


class Command(BaseCommand):
    help = "Importiert Liegenschaften und Montageaufträge aus den HTML-Prototypen."

    def add_arguments(self, parser):
        parser.add_argument("--deckblatt", default=DEFAULT_DIR / "DEMO_Deckblaetter_Dashboard.html",
                            help="Pfad zum Deckblätter-Dashboard (enthält DATA und EMBEDDED_MONTAGE)")
        parser.add_argument("--montage", default=DEFAULT_DIR / "DEMO_Montage_Dashboard.html",
                            help="Pfad zum Montage-Dashboard (Auftragspositionen, Monteure)")
        parser.add_argument("--flush", action="store_true",
                            help="Vorher alle Liegenschaften, Aufträge, Fahrpläne und Konflikte löschen")

    def handle(self, *args, **options):
        deckblatt = Path(options["deckblatt"])
        montage = Path(options["montage"])
        if not deckblatt.exists():
            raise CommandError(f"Datei nicht gefunden: {deckblatt}")

        # The importer puts readers into the "Ableser/Monteur" group.
        call_command("setup_roles", stdout=io.StringIO())  # output not needed here
        if options["flush"]:
            self._flush()

        try:
            data = read_prototypes(
                deckblatt.read_text(encoding="utf-8"),
                montage.read_text(encoding="utf-8") if montage.exists() else None,
            )
        except PrototypeFormatError as error:
            raise CommandError(str(error))
        if not montage.exists():
            self.stdout.write(self.style.WARNING(f"{montage} nicht gefunden: ohne Auftragspositionen."))

        result = import_prototype_data(data)
        conflicts = refresh_conflicts()  # reading vs. installation (conflicts/services.py)
        result.counts["Konflikte / Hinweise Montage"] = conflicts

        for key, number in result.counts.items():
            self.stdout.write(f"  {key}: {number}")
        for warning in result.warnings:
            self.stdout.write(self.style.WARNING(f"  {warning}"))
        self.stdout.write(self.style.SUCCESS("Import abgeschlossen."))

    def _flush(self):
        # Order matters: tours reference buildings/orders (PROTECT).
        Conflict.objects.all().delete()
        Tour.objects.all().delete()
        InstallationOrder.objects.all().delete()
        Building.objects.all().delete()
        PropertyManager.objects.all().delete()
        self.stdout.write("Vorhandene Daten gelöscht.")
