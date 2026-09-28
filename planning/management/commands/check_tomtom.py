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


def key_lines(path):
    """All TOMTOM_API_KEY lines of a file (the LAST one counts)."""
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("TOMTOM_API_KEY")]


def key_line(path):
    """The TOMTOM_API_KEY line that counts (the last one), or None."""
    lines = key_lines(path)
    return lines[-1] if lines else None


def clean(value):
    return value.strip().strip("\"'").strip()


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
        from_file = clean(raw)
        self.ok(f"TOMTOM_API_KEY in .env gefunden ({describe(from_file)})")
        if " " in raw or raw != raw.strip("\"'"):
            self.stdout.write("    Hinweis: Leerzeichen oder Anführungszeichen um den Schlüssel sind unnötig.")
        if len(key_lines(env_file)) > 1:
            self.stdout.write(self.style.WARNING(
                f"    Achtung: TOMTOM_API_KEY steht {len(key_lines(env_file))}× in .env – es gilt nur die LETZTE Zeile. "
                "Bitte alle anderen TOMTOM_API_KEY-Zeilen löschen."))

        self.stdout.write("2. Einstellungen des Servers")
        key = settings.TOMTOM_API_KEY
        if not key:
            self.fail("Die Einstellungen haben keinen Schlüssel geladen.",
                      "Zeile in .env prüfen: genau TOMTOM_API_KEY=… am Zeilenanfang.")
            return
        if key != from_file:
            # load_dotenv() never overwrites a variable that already exists in the environment.
            self.fail(f"Der Server benutzt einen ANDEREN Schlüssel ({describe(key)}) als in .env ({describe(from_file)}).",
                      "Eine Umgebungsvariable TOMTOM_API_KEY überdeckt die Datei .env – z. B. ein Codespaces-Secret "
                      "(github.com → Settings → Codespaces → Secrets) oder ein früheres „export“. "
                      "Im Terminal einmal: unset TOMTOM_API_KEY – bzw. das Secret löschen oder dort den neuen Schlüssel "
                      "eintragen und den Codespace neu starten.")
            return
        self.ok(f"Schlüssel geladen ({describe(key)}) – derselbe wie in .env")
        self.stdout.write("    Vergleiche die ersten 2 Zeichen mit dem Schlüssel auf developer.tomtom.com.")
        odd =sorted({repr(c) for c in key if not c.isascii() or not c.isalnum()})
        if odd:
            self.fail(f"Der Schlüssel enthält ungewöhnliche Zeichen: {', '.join(odd)}",
                      "Beim Kopieren sind unsichtbare Zeichen mitgekommen. Schlüssel auf developer.tomtom.com "
                      "mit dem Kopier-Symbol neu kopieren und in .env ersetzen.")
            return
        if len(key) != 32:
            self.stdout.write(f"    Hinweis: TomTom-Schlüssel sind normalerweise 32 Zeichen lang, dieser hat {len(key)}. "
                              "Vielleicht nicht vollständig kopiert?")
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
            hint = ("TomTom kennt diesen Schlüssel nicht (mehr). Auf developer.tomtom.com → Keys den AKTUELLEN "
                    "Schlüssel mit dem Kopier-Symbol kopieren (nach dem Neu-Erstellen gilt der alte nicht mehr) "
                    "und prüfen, dass „Search API“ und „Routing API“ freigeschaltet sind.")
            if "nicht erreichbar" in str(error):
                hint = "Internetverbindung / Firmen-Netzwerk prüfen (api.tomtom.com muss erreichbar sein)."
            self.fail(str(error), hint)
            return
        self.stdout.write(self.style.SUCCESS("\nAlles in Ordnung – TomTom ist bereit."))
