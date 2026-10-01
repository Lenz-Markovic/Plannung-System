"""Stichtag in planning (pure).

How the office works: a Liegenschaft comes for planning at least 14 days before its Stichtag, because
the Anmeldung / Aushang has to go out 14 days ahead and the reading has to be done by the Stichtag.
Nachablesungen (2. Termin and later) are often months after the Stichtag and are combined with new
readings - they get no warning.
"""

import datetime

NOTICE_DAYS = 14   # the Aushang goes out 14 days before the appointment

WARNING = "warning"
INFO = "info"


def stichtag_findings(day, stichtag, attempt=1, today=None):
    """[(severity, text)] for a reading on `day` of a Liegenschaft with this Stichtag."""
    if stichtag is None or attempt > 1:
        return []
    found = []
    if day > stichtag:
        late = (day - stichtag).days
        found.append((WARNING, f"{late} Tag{'e' if late != 1 else ''} nach dem Stichtag {stichtag:%d.%m.%Y} – "
                               "die Erstablesung sollte bis zum Stichtag erledigt sein"))
    if today is not None and today <= day < today + datetime.timedelta(days=NOTICE_DAYS):
        left = (day - today).days
        when = "der Termin ist heute" if left == 0 else f"nur noch {left} Tag{'e' if left != 1 else ''} bis zum Termin"
        found.append((INFO, f"{when} – ein Aushang 14 Tage vorher geht nicht mehr (Termin telefonisch / per Mail "
                            "vereinbaren?)"))
    return found
