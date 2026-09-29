"""
Give the demo reader (ableser_demo / "Demo-Ableser") a day plan, so "Mein Tag"
can be tried out on the phone:

    python manage.py demo_day            # today
    python manage.py demo_day --datum 2026-10-05

Takes up to 5 buildings that are not planned yet - only as many as fit into
7,5 h - and saves them as a PROVISIONAL tour (the same way the office plans it).
ONLY for local testing.
"""

import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from buildings.models import Building
from planning import services
from planning.models import Employee, Tour
from planning.rules.working_time import fits_in_day


class Command(BaseCommand):
    help = "Legt für den Demo-Ableser einen Fahrplan an (nur zum Testen)."

    def add_arguments(self, parser):
        parser.add_argument("--datum", help="Tag im Format JJJJ-MM-TT (Standard: heute)")
        parser.add_argument("--anzahl", type=int, default=5, help="Anzahl Liegenschaften (Standard: 5)")

    def handle(self, *args, **options):
        employee = Employee.objects.filter(short_name="Demo-Ableser").first()
        if employee is None:
            raise CommandError("Den Demo-Ableser gibt es noch nicht: zuerst python manage.py create_demo_users --password … ausführen.")
        date = datetime.date.fromisoformat(options["datum"]) if options["datum"] else timezone.localdate()
        if Tour.objects.filter(employee=employee, date=date).exists():
            self.stdout.write(f"Der Demo-Ableser hat am {date:%d.%m.%Y} schon einen Fahrplan.")
            return
        # Unplanned buildings of one region, so the drives stay short.
        first = Building.objects.filter(tour_stops__isnull=True).exclude(region="").order_by("file_number").first()
        if first is None:
            raise CommandError("Keine ungeplanten Liegenschaften – zuerst python manage.py import_prototype ausführen.")
        buildings = Building.objects.filter(tour_stops__isnull=True, region=first.region).order_by("zip_code", "file_number")
        # The system chooses the stops itself here, so it keeps to 7,5 h net strictly
        ids, minutes = [], 0
        for building in buildings[: options["anzahl"] * 3]:
            extra = building.reading_minutes + 15  # + rough drive
            if len(ids) < options["anzahl"] and fits_in_day(minutes, extra):
                ids.append(building.pk)
                minutes += extra
        draft = services.create_draft(ids, employee, date, employee.default_start_time, 30, "far")
        tour = services.save_draft(draft, None, confirm=False)
        self.stdout.write(self.style.SUCCESS(
            f"Fahrplan für {employee} am {date:%d.%m.%Y} angelegt ({tour.stops.count()} Stopps, {first.region}). "
            "Jetzt als ableser_demo anmelden → „📱 Mein Tag“."))
