"""Ergebnis of a visit, attempt numbers, when a Nachtermin is needed (pure)."""

import datetime

from planning.rules.visits import (ABSENT, COMPLETE, PARTIAL, VisitInfo, attempt_label, attempt_number,
                                   needs_revisit, report_problems)

D = datetime.date


def test_partial_and_absent_need_a_text():
    assert report_problems(COMPLETE, "") == []
    assert report_problems(PARTIAL, "  ") and report_problems(PARTIAL, "NE003 fehlt") == []
    assert len(report_problems(ABSENT, "", "")) == 2
    assert report_problems(ABSENT, "neuen Termin per Aushang", "absent") == []
    assert report_problems("?", "x") == ["Bitte ein Ergebnis wählen."]


def test_attempts_are_counted():
    assert attempt_number([], D(2026, 10, 5)) == 1
    assert attempt_number([D(2026, 10, 5)], D(2026, 10, 20)) == 2
    assert attempt_number([D(2026, 10, 5), D(2026, 10, 20)], D(2026, 11, 3)) == 3
    assert attempt_number([D(2026, 10, 5)], D(2026, 10, 5)) == 1  # the same day is not "earlier"
    assert attempt_label(1) == "1. Termin" and attempt_label(3) == "3. Termin (Nachtermin)"


def test_when_a_nachtermin_is_needed():
    first = VisitInfo(D(2026, 10, 5), PARTIAL)
    assert needs_revisit([first], []) is True
    assert needs_revisit([first], [D(2026, 10, 20)]) is False           # already planned again
    assert needs_revisit([first], [D(2026, 9, 1)]) is True               # an older plan does not count
    assert needs_revisit([first, VisitInfo(D(2026, 10, 20), COMPLETE)], []) is False  # done the 2nd time
    assert needs_revisit([VisitInfo(D(2026, 10, 5), ABSENT, closed=True)], []) is False  # office: no more
    assert needs_revisit([], []) is False
