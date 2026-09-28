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
