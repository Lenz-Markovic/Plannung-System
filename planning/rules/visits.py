"""
What happened at a visit, and what comes after it (pure functions, no database).

After a reading / installation the Ableser/Monteur reports an Ergebnis:
  complete  ✓ fertig (100 %)
  partial   ◐ teilweise erledigt - MUST write what is still to do (e.g. "NE003, NE007 fehlen")
  absent    ✗ niemand angetroffen / nicht möglich - reason + what to do next

partial / absent mean: a Nachtermin (next visit) is needed. The visits of one object are
counted: 1. Termin, 2. Termin (Nachtermin), 3. Termin ... so the planner knows how often
somebody went there already.
"""

from dataclasses import dataclass

COMPLETE, PARTIAL, ABSENT = "complete", "partial", "absent"
OUTCOMES = {COMPLETE: "✓ fertig (100 %)", PARTIAL: "◐ teilweise erledigt", ABSENT: "✗ nicht erledigt"}
OUTCOME_ICONS = {COMPLETE: "✓", PARTIAL: "◐", ABSENT: "✗"}

REASONS = [
    ("absent", "Niemand angetroffen"),
    ("no_access", "Kein Zugang (Schlüssel / Heizraum)"),
    ("refused", "Mieter hat abgelehnt"),
    ("defect", "Gerät defekt / fehlt"),
    ("no_time", "Nicht geschafft / Termin ausgefallen"),
    ("other", "Sonstiges"),
]
REASON_LABELS = dict(REASONS)


def report_problems(outcome, todo, reason=""):
    """What is missing in a report? [] = ok."""
    problems = []
    if outcome not in OUTCOMES:
        problems.append("Bitte ein Ergebnis wählen.")
    if outcome in (PARTIAL, ABSENT) and not (todo or "").strip():
        problems.append("Bitte eintragen, was noch zu tun ist (z. B. welche Wohnungen fehlen).")
    if outcome == ABSENT and reason not in REASON_LABELS:
        problems.append("Bitte einen Grund wählen.")
    return problems


def attempt_number(earlier_visit_dates, date):
    """1 for the first visit, 2 for the first Nachtermin, ... (visits BEFORE this day count)."""
    return sum(1 for d in earlier_visit_dates if d < date) + 1


@dataclass(frozen=True)
class VisitInfo:
    date: object
    outcome: str
    closed: bool = False  # the office decided: no further visit
    seq: int = 0          # order of reports on the same day (e.g. the database id)


def attempt_in_series(earlier):
    """Which Termin is the next visit? earlier: VisitInfo in reporting order (date, seq).

    A complete visit, or one the office closed, ends a series: afterwards it is the 1. Termin
    again (e.g. next year's reading). Otherwise every unsuccessful visit counts: 2., 3. Termin ...
    """
    series = 0
    for visit in sorted(earlier, key=lambda v: (v.date, v.seq)):
        series = 0 if (visit.outcome == COMPLETE or visit.closed) else series + 1
    return series + 1


def needs_revisit(visits, planned_dates):
    """visits: VisitInfo of one object; planned_dates: days of planned, not yet visited stops.

    A Nachtermin is needed when the LAST visit was not complete, nobody closed it,
    and there is no next appointment planned after it yet.
    """
    if not visits:
        return False
    last = max(visits, key=lambda v: (v.date, v.seq))  # the last REPORTED one, also on the same day
    if last.outcome == COMPLETE or last.closed:
        return False
    return not any(d >= last.date for d in planned_dates)


def attempt_label(number):
    return "1. Termin" if number == 1 else f"{number}. Termin (Nachtermin)"
