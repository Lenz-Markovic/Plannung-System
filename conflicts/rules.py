"""
Conflict rules between reading and installation (spec section 8).

Pure functions: plain dates in, a result out. No database, no request.
- check_installation_vs_reading: ONE reading date against ONE installation date
- planning_findings: warnings while planning a tour
- evaluate_installations: all rules of section 8 for one building and its
  orders (stored in the Conflict table by conflicts/services.py)
"""

import datetime
import re
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


# --- Full check: all installation orders of ONE building (bewerteMontage) --------

HINT = "hint"  # conflicts.models.Severity.HINT (not used by the prototype rules)

# Rule codes, same values as conflicts.models.ConflictRule
INSTALL_AFTER_READING = "install_after_reading"
SAME_DAY = "same_day"
BUFFER_SHORT = "buffer_short"
RADIO_RETROFIT = "radio_retrofit"
SAME_PERSON = "same_person"
GATEWAY_NOT_MARKED = "gateway_not_marked"
ORDER_WITHOUT_DATE = "order_without_date"
NO_READING_DATE = "no_reading_date"

RADIO_KINDS = {"FUNKMODUL", "GATEWAY", "EHKV", "PULS"}  # an order with these makes the building "Funk"
SEVERITY_RANK = {OK: 1, INFO: 2, WARNING: 3, CRITICAL: 4}


@dataclass(frozen=True)
class Reading:
    """The planned reading of a building (DATA row of the prototype)."""

    date: datetime.date | None
    reader: str = ""            # short name of the reader, "" = open
    reading_type: str = ""      # e.g. "MANU - Betreten der Wohnung"
    minutes: int = 0            # calculated reading time
    has_gateway: bool = False


@dataclass(frozen=True)
class Order:
    """One installation order that belongs to the building."""

    re_number: str
    via: str                            # "RE-Nr." or "Liegenschaftsnummer"
    date: datetime.date | None = None   # planned installation day
    time: datetime.time | None = None
    installer: str = ""                 # first installer, "" = open
    done: bool = False
    kinds: tuple = ()                   # device category codes, e.g. ("EHKV", "GATEWAY")
    summary: str = ""                   # e.g. "17× EHKV, 1× Gateway"


@dataclass(frozen=True)
class Message:
    rule: str        # "" for "ok" messages
    severity: str    # ok / info / warning / critical
    text: str
    re_number: str
    via: str


def _de(date, time=None):
    """Date as the prototype prints it: 7.12.2026 (no leading zeros), optional ', 08:00 Uhr'."""
    text = f"{date.day}.{date.month}.{date.year}"
    return f"{text}, {time:%H:%M} Uhr" if time else text


def _minutes(value):
    """fmtZeit(): 95 -> '1h 35min', 40 -> '40min'."""
    hours, rest = divmod(round(value), 60)
    return f"{hours}h {rest}min" if hours > 0 else f"{rest}min"


def evaluate_installations(reading, orders):
    """All messages for one building (port of bewerteMontage() in the prototype).

    Per order:
      done                               -> ok
      no installation date               -> info  (order without date)
      no reading date                    -> info
      installation AFTER the reading     -> critical
      same day                           -> critical
      1-7 days before the reading        -> warning, more -> ok
        + radio devices but manual reading type  -> info
        + installer = reader, <= 3 days apart    -> info
      gateway ordered, building not marked       -> info
    """
    messages = []
    manual = re.search(r"Betreten|manuelle Ablesung|^MANU", reading.reading_type or "", re.IGNORECASE) is not None
    reading_day = _de(reading.date) if reading.date else ""
    reader = reading.reader or "kein Ableser"

    for order in orders:
        def add(rule, severity, text):
            messages.append(Message(rule, severity, text, order.re_number, order.via))

        when = _de(order.date, order.time) if order.date else ""
        installer = f" · Monteur {order.installer}" if order.installer else " · Monteur offen"
        same_person = bool(order.installer and reading.reader and order.installer == reading.reader)
        radio = bool(RADIO_KINDS & set(order.kinds))
        what = f" ({order.summary})" if order.summary else ""

        if order.done:
            add("", OK, f"{order.re_number}: Montage erledigt{' am ' + when if order.date else ''}{what}.")
        elif order.date is None:
            add(ORDER_WITHOUT_DATE, INFO, f"{order.re_number}: Umrüstung noch ohne Montagetermin{what}" + (
                " — für diese Liegenschaft ist auch kein Ablesetag hinterlegt." if reading.date is None else
                f" — Ablesetag ist der {reading_day} ({reader}); sobald der Montagetermin steht, hier prüfen."))
        elif reading.date is None:
            add(NO_READING_DATE, INFO, f"{order.re_number}: Montage {when}{installer}"
                                       " — für diese Liegenschaft ist kein Ablesetag hinterlegt (Route offen).")
        elif order.date > reading.date:
            days = (order.date - reading.date).days
            add(INSTALL_AFTER_READING, CRITICAL,
                f"{order.re_number}: Montage {when}{installer} liegt {days} Tag(e) NACH der geplanten Ablesung "
                f"{reading_day} ({reader}){what} — Ablesetour verschieben oder Zwischenablesung einplanen.")
        elif order.date == reading.date:
            add(SAME_DAY, CRITICAL, f"{order.re_number}: Montage und Ablesung am selben Tag ({when})" + (
                " — Monteur und Ableser sind dieselbe Person, Termine zusammenlegen." if same_person else
                f" — Monteur {order.installer or 'offen'} / Ableser {reading.reader or 'offen'}, Reihenfolge und Zugang klären."))
        else:
            days = (reading.date - order.date).days
            if days <= 7:
                add(BUFFER_SHORT, WARNING, f"{order.re_number}: nur {days} Tag(e) zwischen Montage {when}{installer} "
                                           f"und Ablesung {reading_day} — Puffer knapp, Verschiebung würde die Tour treffen.")
            else:
                add("", OK, f"{order.re_number}: Montage {when}{installer}, {days} Tage vor der Ablesung "
                            f"{reading_day} — Reihenfolge passt.")
            if radio and manual:
                add(RADIO_RETROFIT, INFO, f"{order.re_number}: nach der Umrüstung auf Funk ist die Ableseart "
                                          f"„{reading.reading_type.split(' - ')[0]}“ evtl. nicht mehr nötig — "
                                          f"Ablesetour und Zeitansatz ({_minutes(reading.minutes)}) prüfen.")
            if same_person and days <= 3:
                add(SAME_PERSON, INFO, f"{order.re_number}: {order.installer} ist hier auch als Ableser eingeteilt"
                                       " — Montage und Ablesung ggf. in einem Termin.")
        if "GATEWAY" in order.kinds and not reading.has_gateway:
            add(GATEWAY_NOT_MARKED, INFO, f"{order.re_number}: Gateway-Montage vorgesehen, die Liegenschaft ist hier "
                                          "noch nicht als Gateway-Anlage markiert.")
    return messages


def worst(messages):
    """Highest severity of the messages ('' if there are none)."""
    return max((m.severity for m in messages), key=SEVERITY_RANK.get, default="")
