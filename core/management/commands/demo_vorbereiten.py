"""
Prepare a fresh demo for a presentation: everything from the start, with a few examples on every page.

    python manage.py demo_vorbereiten --password "Test-Passwort-2026"

1. roles + one test user per role (create_demo_users)
2. demo data from docs/prototype/ (import_prototype --flush: deletes buildings, orders and plans first);
   the plans are moved by whole weeks so the readings start a week before today (past and coming days)
3. a day plan for the demo reader today (demo_day) -> "📱 Mein Tag"
4. examples: results of past appointments (Rückmeldungen, Nachtermine), Aushänge to print / printed /
   Briefe for single flats, notes
ONLY for local testing - never run this on a real server (it deletes the data).
"""

import datetime
import io

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.models import F
from django.utils import timezone

from documents import notice_rules as rules
from documents import notices
from journal.models import Activity, Note, NoteKind
from journal.notes import add_note
from planning import visits
from planning.models import Absence, StopKind, Tour, TourStop
from planning.rules.visits import ABSENT, COMPLETE, PARTIAL

# outcome, what is still to do, reason - in turn for the past appointments
RESULTS = [
    (COMPLETE, "", ""), (COMPLETE, "", ""), (PARTIAL, "2 Heizkostenverteiler im Keller nicht erreichbar", ""),
    (COMPLETE, "", ""), (ABSENT, "Alle Wohnungen – niemand angetroffen", "absent"), (COMPLETE, "", ""), (PARTIAL, "Wohnung 4 war verschlossen", ""),
]
NOTES = [
    (NoteKind.ACCESS, "Schlüssel beim Hausmeister, Herr Kaya, EG links (Tel. im Auftrag)"),
    (NoteKind.WISH, "Mieterin Whg 3 bittet um einen Termin nach 15 Uhr"),
    (NoteKind.INFO, "Hof-Einfahrt nur bis 3,5 t – mit dem kleinen Wagen fahren"),
]


class Command(BaseCommand):
    help = "Bereitet eine frische Vorführung vor (löscht die Demodaten und legt sie neu an – nur lokal)."

    def add_arguments(self, parser):
        parser.add_argument("--password", required=True, help="Passwort für alle Test-Benutzer")

    def handle(self, *args, **options):
        quiet = io.StringIO()
        say = self.stdout.write
        say("1/4 Rollen und Test-Benutzer …")
        call_command("migrate", verbosity=0)
        call_command("create_demo_users", password=options["password"], stdout=quiet)
        say("2/4 Demodaten neu einlesen (dauert etwas) …")
        Activity.objects.all().delete()   # the Verlauf starts empty, too
        Note.objects.all().delete()
        call_command("import_prototype", flush=True, stdout=quiet)
        weeks = self._shift()
        if weeks:
            say(f"    Fahrpläne um {abs(weeks)} Wochen {'vorgezogen' if weeks < 0 else 'nach hinten geschoben'}")
        say("3/4 Fahrplan für den Demo-Ableser heute …")
        call_command("demo_day", stdout=quiet)
        say("4/4 Beispiele: Rückmeldungen, Aushänge, Notizen …")
        office = User.objects.get(username="admin_demo")
        reported = self._results()
        wanted, printed, letters = self._notices(office)
        self._notes(office)
        say(self.style.SUCCESS(
            f"Fertig: {reported} Ergebnisse gemeldet, {wanted} Aushänge nötig (davon {printed} gedruckt, "
            f"{letters} als Briefe), {len(NOTES)} Notizen.\n"
            f"Anmelden z. B. als dispo_demo / sachbearbeitung_demo / ableser_demo / leitung_demo / admin_demo "
            f"mit dem Passwort {options['password']}"))

    def _shift(self):
        """Move all plans (and absences) by whole weeks, so the first reading day is about a week ago."""
        first = Tour.objects.filter(stops__kind=StopKind.READING).order_by("date").values_list("date", flat=True).first()
        if first is None:
            return 0
        weeks = round(((timezone.localdate() - datetime.timedelta(days=7)) - first).days / 7)
        if weeks:
            # in two steps, so no plan lands on the day of another one of the same person on the way
            far = datetime.timedelta(days=7 * 1500)
            for model, fields in ((Tour, ["date"]), (Absence, ["start_date", "end_date"])):
                model.objects.update(**{f: F(f) + far for f in fields})
                model.objects.update(**{f: F(f) - far + datetime.timedelta(weeks=weeks) for f in fields})
            call_command("update_conflicts", stdout=io.StringIO())
        return weeks

    def _results(self):
        """The readers report the appointments of the last days (as in 📱 Mein Tag)."""
        today = timezone.localdate()
        stops = (TourStop.objects.filter(tour__date__lt=today, tour__date__gte=today - datetime.timedelta(days=21),
                                         kind=StopKind.READING, tour__employee__user__isnull=False)
                 .select_related("tour__employee__user").order_by("-tour__date", "position"))
        done = 0
        for stop, (outcome, todo, reason) in zip(stops, RESULTS * 3):
            visits.report(stop, stop.tour.employee.user, outcome, todo, reason, on_behalf=False)
            done += 1
        return done

    def _notices(self, office):
        """Aushänge in the plans 15-40 days ahead (in time: 14 days before), one too late on purpose:
        some still to print, some printed, one as Briefe for single flats."""
        today = timezone.localdate()
        coming = Tour.objects.filter(stops__kind=StopKind.READING).distinct().order_by("date", "pk")
        late = list(coming.filter(date__gt=today, date__lte=today + datetime.timedelta(days=14))[:1])
        in_time = list(coming.filter(date__gt=today + datetime.timedelta(days=14),
                                     date__lte=today + datetime.timedelta(days=40))[:9])
        stops = [s for tour in late + in_time for s in tour.stops.filter(kind=StopKind.READING).order_by("position")[:2]]
        for stop in stops:
            notices.set_wanted(stop, True)
        letters = stops[len(late) * 2] if len(stops) > len(late) * 2 else None
        if letters is not None:
            notices.set_notice(letters, office, scope=rules.SOME_UNITS, units="Whg 3 (Müller), Whg 7",
                               window_from="8:00", window_to="12:00")
        printed = [s for s in stops[::2] if s is not letters]   # every second one, the Briefe stay "still to print"
        notices.mark_printed(printed, office)
        return len(stops), len(printed), 1 if letters is not None else 0

    def _notes(self, office):
        today = timezone.localdate()
        stops = TourStop.objects.filter(tour__date__gte=today, kind=StopKind.READING).select_related("building")
        for (kind, text), stop in zip(NOTES, stops.order_by("tour__date", "position")[3:6]):
            add_note(office, text, kind, building=stop.building)
