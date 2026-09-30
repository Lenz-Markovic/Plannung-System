"""🧾 Rückmeldungen: states, filters, next steps, hints (pure)."""

import datetime
import itertools

from planning.rules.followup import (CHECK, DONE, LATER, MISSING, PLANNED, PROBLEM, REVISIT, chosen_filter,
                                     close_problems, count_filters, hints, in_filter, is_open, is_quiet,
                                     missing_report, next_step, office_state)
from planning.rules.visits import ABSENT, COMPLETE, PARTIAL, VisitInfo, needs_revisit

D = datetime.date(2026, 9, 28)


def test_complete_waits_for_check_until_closed_or_object_done():
    assert office_state(COMPLETE, False, True, False, False) == CHECK
    assert office_state(COMPLETE, True, True, False, False) == DONE
    assert office_state(COMPLETE, False, True, False, True) == DONE
    assert office_state(COMPLETE, False, True, True, False) == CHECK  # never "Nachtermin geplant"


def test_partial_and_absent_need_revisit_until_planned_or_closed():
    assert office_state(PARTIAL, False, True, False, False) == REVISIT
    assert office_state(ABSENT, False, True, True, False) == PLANNED
    assert office_state(ABSENT, True, True, False, False) == DONE
    assert office_state(PARTIAL, False, True, False, True) == REVISIT  # object done is ignored here on purpose


def test_older_visit_is_later_and_closed_wins():
    assert office_state(PARTIAL, False, False, False, False) == LATER
    assert office_state(PARTIAL, True, False, False, False) == DONE


def test_revisit_equals_needs_revisit():
    for outcome, closed, planned in itertools.product([COMPLETE, PARTIAL, ABSENT], [False, True],
                                                      [[], [D - datetime.timedelta(days=3)], [D], [D + datetime.timedelta(days=5)]]):
        planned_again = any(d >= D for d in planned)
        state = office_state(outcome, closed, True, planned_again, False)
        assert (state == REVISIT) == needs_revisit([VisitInfo(D, outcome, closed)], planned), (outcome, closed, planned)


def test_missing_report_window_and_superseded():
    today = D
    assert not missing_report(today, today)                                   # today is still being worked on
    assert missing_report(today - datetime.timedelta(days=1), today)
    assert missing_report(today - datetime.timedelta(days=30), today)
    assert not missing_report(today - datetime.timedelta(days=31), today)
    yesterday = today - datetime.timedelta(days=1)
    assert not missing_report(yesterday, today, later_planned=True)
    assert not missing_report(yesterday, today, later_visit=True)
    assert not missing_report(yesterday, today, object_done=True)


def test_is_open_and_filters():
    assert is_open(CHECK) and is_open(DONE, has_problem=True) and not is_open(PLANNED)
    assert in_filter("pruefen", CHECK) and in_filter("nachtermin", REVISIT) and in_filter("ohne", MISSING)
    assert in_filter("probleme", DONE, True) and in_filter("probleme", PROBLEM)
    assert in_filter("geplant", PLANNED) and in_filter("erledigt", LATER) and not in_filter("erledigt", DONE, True)
    assert in_filter("?", CHECK) and not in_filter("?", PLANNED)  # unknown key = offen
    assert chosen_filter("", True) == "alle" and chosen_filter("", False) == "offen" and chosen_filter("pruefen", True) == "pruefen"


def test_count_filters():
    counts = count_filters([(CHECK, False), (REVISIT, False), (DONE, True), (PLANNED, False)])
    assert counts["offen"] == 3 and counts["probleme"] == 1 and counts["geplant"] == 1 and counts["alle"] == 4
    assert counts["erledigt"] == 0


def test_next_step_by_state_and_permissions():
    assert next_step(CHECK, "reading", has_documents=True, can_release=True)[1] == "release"
    text, primary = next_step(CHECK, "reading", has_documents=True)
    assert "macht die Sachbearbeitung" in text and primary == ""
    assert next_step(CHECK, "reading", can_process=True)[1] == "check"
    assert "Nacharbeit klären" in next_step(CHECK, "reading", building_status="rework")[0]
    assert next_step(CHECK, "installation", can_finish_order=True)[1] == "finish_order"
    assert "Disposition" in next_step(CHECK, "installation")[0]
    assert next_step(REVISIT, "reading", can_plan=True)[1] == "plan"
    assert next_step(REVISIT, "reading", has_wish=True, can_process=True)[1] == ""
    assert next_step(REVISIT, "reading", can_process=True)[1] == "wish"
    assert next_step(CHECK, "reading", has_problem=True, can_resolve=True, can_process=True)[1] == "resolve"
    assert next_step(MISSING, "reading", can_process=True)[1] == "report" and next_step(MISSING, "reading")[1] == ""
    for state in (CHECK, REVISIT, MISSING, PROBLEM, PLANNED):
        assert next_step(state, "reading")[1] == ""  # Leitung: never a button


def test_hints():
    assert "2. Termin ohne Erfolg" in hints(REVISIT, "reading", ABSENT, 2)[0]
    assert hints(REVISIT, "installation", ABSENT, 3) == []
    assert "Storno" in hints(REVISIT, "reading", PARTIAL, 1, storno=True)[0]
    assert hints(CHECK, "reading", COMPLETE, 1) == ["📄 Unterlagen (Kosten) fehlen noch"]
    assert "ist vorbei" in hints(PLANNED, "reading", PARTIAL, 1, planned_on=D, today=D + datetime.timedelta(days=1))[0]


def test_is_quiet_only_plain_readings():
    assert is_quiet(CHECK, "reading", "")
    assert not is_quiet(CHECK, "reading", "Zähler neu")
    assert not is_quiet(CHECK, "reading", "", has_problem=True)
    assert not is_quiet(CHECK, "reading", "", has_proposal=True)
    assert not is_quiet(CHECK, "reading", "", has_documents=True)
    assert not is_quiet(CHECK, "reading", "", building_status="rework")
    assert not is_quiet(CHECK, "installation", "")


def test_close_problems():
    assert close_problems(PARTIAL, " ") and close_problems(ABSENT, "")
    assert close_problems(COMPLETE, "") == [] and close_problems(PARTIAL, "telefonisch geklärt") == []
    assert close_problems(COMPLETE, "x" * 301) == ["Höchstens 300 Zeichen."]
