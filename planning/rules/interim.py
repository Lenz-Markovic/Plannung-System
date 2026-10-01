"""🔄 Zwischenablesung (Nutzerwechsel) - pure rules.

Mostly the Hausverwaltung sends a mail: a tenant moves out on a date. For that Liegenschaft the office
chooses the flats and, per flat, what is needed (HKV ablesen, Ampullen tauschen, Wasser, Wärme, RWM).
It is planned like a short reading with a Brief to those flats - it does not touch the main readout.
"""

import datetime
import math

NEEDS = [
    ("hkv", "HKV ablesen", 5),
    ("ampullen", "🧪 Ampullen tauschen", 5),
    ("wwz", "WWZ (warm)", 2),
    ("kwz", "KWZ (kalt)", 2),
    ("wmz", "WMZ (Wärme)", 3),
    ("rwm", "RWM prüfen", 2),
]
NEED_LABELS = {code: label for code, label, _ in NEEDS}
NEED_MINUTES = {code: minutes for code, _, minutes in NEEDS}
FLAT_MINUTES = 10           # ringing, getting in, Ablesebeleg per flat

SOURCES = [("mail_hv", "📧 Mail der Hausverwaltung"), ("telefon", "☎ Anruf"), ("brief", "✉ Brief"), ("sonstiges", "sonstiges")]

OPEN, PLANNED, DONE, PARTIAL, CANCELLED = "offen", "geplant", "erledigt", "teilweise", "storniert"
STATE_LABELS = {OPEN: "offen – noch einplanen", PLANNED: "📅 geplant", DONE: "✓ erledigt",
                PARTIAL: "◐ nicht alles – nochmal einplanen", CANCELLED: "✕ storniert"}


def offered_needs(hkv_count, verdunster, wwz_count, kwz_count, wmz_count, has_rwm):
    """Only what the Liegenschaft has: [(code, label)]."""
    have = {"hkv": hkv_count > 0, "ampullen": verdunster and hkv_count > 0, "wwz": wwz_count > 0,
            "kwz": kwz_count > 0, "wmz": wmz_count > 0, "rwm": bool(has_rwm)}
    return [(code, label) for code, label, _ in NEEDS if have[code]]


def default_needs(offered):
    """Preticked for a new flat: everything except the RWM check."""
    return [code for code, _ in offered if code != "rwm"]


def clean_flats(rows):
    """[(unit, tenant, needs)] from the form -> [{"unit", "tenant", "needs"}], empty rows dropped."""
    flats = []
    for unit, tenant, needs in rows:
        unit, tenant = " ".join((unit or "").split()), " ".join((tenant or "").split())
        needs = [n for n in needs if n in NEED_LABELS]
        if unit or tenant or needs:
            flats.append({"unit": unit[:60], "tenant": tenant[:120], "needs": needs})
    return flats


def problems(flats, move_date):
    found = []
    if move_date is None:
        found.append("Bitte das Datum des Nutzerwechsels eintragen.")
    if not flats:
        found.append("Bitte mindestens eine Wohnung eintragen.")
    for flat in flats:
        if not flat["unit"]:
            found.append("Bei jeder Zeile bitte die Wohnung eintragen (z. B. Whg 3 oder NE003).")
            break
    for flat in flats:
        if not flat["needs"]:
            found.append(f"{flat['unit'] or 'Wohnung'}: bitte ankreuzen, was gemacht werden muss.")
            break
    return found


def minutes(flats):
    """Work time, rounded up to 10."""
    raw = sum(FLAT_MINUTES + sum(NEED_MINUTES.get(n, 0) for n in flat["needs"]) for flat in flats)
    return max(10, math.ceil(raw / 10) * 10)


def units_text(flats):
    """'Whg 3 (Müller), Whg 7' - one Brief each."""
    return ", ".join(f"{f['unit']} ({f['tenant']})" if f["tenant"] else f["unit"] for f in flats if f["unit"])


def state(cancelled, stop_outcome, planned):
    """stop_outcome: of the latest stop ('' = not reported yet); planned: a stop exists."""
    if cancelled:
        return CANCELLED
    if stop_outcome == "complete":
        return DONE
    if stop_outcome in ("partial", "absent"):
        return PARTIAL
    return PLANNED if planned else OPEN


def due_hint(move_date, today):
    """The reading should be close to the move date."""
    if move_date is None:
        return ""
    days = (move_date - today).days
    if days < 0:
        return f"Nutzerwechsel war vor {-days} Tag{'en' if -days != 1 else ''}"
    if days == 0:
        return "Nutzerwechsel heute"
    return f"Nutzerwechsel in {days} Tag{'en' if days != 1 else ''}"


def suggested_day(move_date, today):
    """The move date (not in the past, not on a weekend)."""
    day = max(move_date or today, today + datetime.timedelta(days=1))
    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    return day
