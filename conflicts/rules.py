"""
Conflict rules between reading and installation (spec section 8).

Pure functions: plain dates in, a result out. No database, no request.
This first function compares ONE reading date with ONE installation date;
the full conflict check (all rules of section 8, stored in the Conflict
table) builds on it in a later step.
"""

import datetime
from dataclasses import dataclass

# Same words as conflicts.models.Severity; "ok" = "in Ordnung" (not stored)
CRITICAL = "critical"
WARNING = "warning"
INFO = "info"
OK = "ok"


@dataclass(frozen=True)
class DateCheck:
    severity: str
    days: int | None  # days between the two dates, None if one is missing
    message: str


def check_installation_vs_reading(reading_date: datetime.date | None,
                                  installation_date: datetime.date | None) -> DateCheck:
    """Is the installation early enough before the reading?

    Texts as in tmFussnote() of the prototype:
      installation AFTER reading   -> critical (new devices would not be read)
      same day                     -> critical
      1-7 days before the reading  -> warning  (small buffer)
      more than 7 days before      -> ok
    """
    if installation_date is None:
        return DateCheck(INFO, None, "Montagetermin noch offen")
    if reading_date is None:
        return DateCheck(INFO, None, "kein Ablesetag zum Vergleich")

    days = abs((reading_date - installation_date).days)
    if installation_date > reading_date:
        return DateCheck(CRITICAL, days, f"Montage {days} Tage NACH Ablesung — Tour verschieben")
    if installation_date == reading_date:
        return DateCheck(CRITICAL, 0, "Montage und Ablesung am selben Tag")
    if days <= 7:
        return DateCheck(WARNING, days, f"nur {days} Tag{'' if days == 1 else 'e'} Puffer vor der Ablesung")
    return DateCheck(OK, days, f"Montage {days} Tage vor Ablesung")


# --- Conflicts while planning a tour (konflikteFuer() in the prototype) ---------

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _day(date):
    return f"{WEEKDAYS[date.weekday()]} {date:%d.%m.%Y}"


@dataclass(frozen=True)
class OtherPlan:
    """The building is already in another tour."""

    person: str
    date: datetime.date
    provisional: bool


@dataclass(frozen=True)
class PlannedBuilding:
    """What the check needs to know about one building of the new plan."""

    key: object                 # e.g. the building id
    file_number_core: str
    other_plans: tuple = ()     # OtherPlan, excluding the tour being planned
    installation_dates: tuple = ()  # planned installations of its orders


@dataclass(frozen=True)
class Finding:
    severity: str
    message: str
    moves_stop: bool = False  # "already in another plan": saving moves it here


def planning_findings(buildings, date, absent=False):
    """Findings per building key for a tour on `date`.

    - selected twice (same building number core)         -> critical
    - already in another tour (saving moves it here)       -> critical
    - installation on/after the new reading day, <= 7 days -> as in section 8
    - the person is absent that day                        -> critical (all)
    """
    findings = {b.key: [] for b in buildings}
    seen = {}
    for b in buildings:
        if b.file_number_core in seen:
            findings[b.key].append(Finding(CRITICAL, f"doppelt ausgewählt (gleiche Liegenschaft wie {seen[b.file_number_core]})"))
        else:
            seen[b.file_number_core] = b.file_number_core
        for plan in b.other_plans:
            state = "vorläufig" if plan.provisional else "bestätigt"
            findings[b.key].append(Finding(
                CRITICAL, f"schon im Fahrplan {plan.person} am {_day(plan.date)} ({state}) – wird beim Speichern hierher verschoben",
                moves_stop=True,
            ))
        for installation in b.installation_dates:
            check = check_installation_vs_reading(date, installation)
            if check.severity in (CRITICAL, WARNING):
                findings[b.key].append(Finding(check.severity, f"Montage {installation:%d.%m.%Y}: {check.message}"))
        if absent:
            findings[b.key].append(Finding(CRITICAL, "Mitarbeiter ist an diesem Tag abwesend"))
    return findings
