"""
Check the TomTom connection step by step:

    python manage.py check_tomtom

1. Is there a .env file, and does it contain TOMTOM_API_KEY?
2. Did the server settings load the key? (the key itself is never printed)
3. Does TomTom accept the key? (one address search, one route)
"""

import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from planning.tomtom import TomTomClient, TomTomError

TEST_ADDRESS = "Mörikeweg 8, 71154 Nufringen"


def key_line(path):
    """The TOMTOM_API_KEY line of a file, or None."""
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("TOMTOM_API_KEY"):
            return line.strip()
    return None


def describe(value):
    """'32 Zeichen, beginnt mit Rw…' - enough to compare, without showing the key."""
    return f"{len(value)} Zeichen, beginnt mit „{value[:2]}…“"


class Command(BaseCommand):
    help = "Prüft Schritt für Schritt, ob der TomTom-Schlüssel funktioniert."

    def ok(self, text):
        self.stdout.write(self.style.SUCCESS(f"  ✓ {text}"))

    def fail(self, text, hint):
        self.stdout.write(self.style.ERROR(f"  ✗ {text}"))
        self.stdout.write(f"    → {hint}")

    def handle(self, *args, **options):
        base = Path(settings.BASE_DIR)
        env_file, example_file = base / ".env", base / ".env.example"

        self.stdout.write("1. Datei .env")
        line = key_line(env_file)
        if not env_file.exists():
            self.fail(".env gibt es nicht.", "Im Terminal: cp .env.example .env – dann den Schlüssel in .env eintragen.")
            return
        if line is None or line.split("=", 1)[1].strip() in ("", '""', "''"):
            example = key_line(example_file)
            hint = "In .env die Zeile TOMTOM_API_KEY=dein-schlüssel eintragen (ohne Leerzeichen, ohne Anführungszeichen)."
            if example and example.split("=", 1)[1].strip():
                hint += " Achtung: der Schlüssel steht in .env.example – der gehört aber in .env!"
            self.fail("In .env steht kein TOMTOM_API_KEY.", hint)
            return
        raw = line.split("=", 1)[1].strip()
        self.ok(f"TOMTOM_API_KEY in .env gefunden ({describe(raw.strip(chr(34) + chr(39)))})")
        if " " in raw or raw != raw.strip("\"'"):
            self.stdout.write("    Hinweis: Leerzeichen oder Anführungszeichen um den Schlüssel sind unnötig.")

        self.stdout.write("2. Einstellungen des Servers")
        key = settings.TOMTOM_API_KEY
        if not key:
            self.fail("Die Einstellungen haben keinen Schlüssel geladen.",
                      "Zeile in .env prüfen: genau TOMTOM_API_KEY=… am Zeilenanfang.")
            return
        self.ok(f"Schlüssel geladen ({describe(key)})")
        self.stdout.write("    Wichtig: Nach jeder Änderung an .env den Server neu starten "
                          "(Strg + C, dann python manage.py runserver).")

        self.stdout.write("3. Verbindung zu TomTom")
        client = TomTomClient(key)
        try:
            place = client.geocode(TEST_ADDRESS)
            self.ok(f"Adresssuche funktioniert: {place.label} ({place.latitude:.4f}, {place.longitude:.4f})")
            tomorrow_9 = timezone.make_aware(datetime.datetime.combine(
                timezone.localdate() + datetime.timedelta(days=1), datetime.time(9, 0)))
            leg = client.route((place.latitude, place.longitude), (48.7758, 9.1829), tomorrow_9)
            self.ok(f"Routenberechnung funktioniert: Nufringen → Stuttgart {round(leg.seconds / 60)} min, {leg.meters / 1000:.1f} km")
        except TomTomError as error:
            hint = "Schlüssel auf developer.tomtom.com prüfen (gültig? neu erstellt? richtig kopiert?)."
            if "nicht erreichbar" in str(error):
                hint = "Internetverbindung / Firmen-Netzwerk prüfen (api.tomtom.com muss erreichbar sein)."
            self.fail(str(error), hint)
            return
        self.stdout.write(self.style.SUCCESS("\nAlles in Ordnung – TomTom ist bereit."))
