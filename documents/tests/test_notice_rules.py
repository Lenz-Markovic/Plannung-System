import datetime

import pytest

from documents.notice_rules import LATE, MISSING, OUTDATED, PRINTED, notice_state, notice_text, notice_window

T = datetime.time
DAY = datetime.date(2026, 11, 3)  # a Tuesday


@pytest.mark.parametrize("start, end, expected", [
    (T(9, 25), T(10, 5), (T(9), T(11))),     # at least 2 hours
    (T(8, 0), T(12, 40), (T(8), T(13))),     # end rounded up to the full hour
    (T(14, 0), T(15, 0), (T(14), T(16))),
    (T(22, 30), T(23, 10), (T(22), T(23))),  # never after 23:00
])
def test_window(start, end, expected):
    assert notice_window(start, end) == expected


def test_window_without_times():
    assert notice_window(None, None) is None


def test_text():
    assert notice_text(DAY, (T(9), T(11))) == "Dienstag, 03.11.2026, zwischen 09:00 und 11:00 Uhr"
    assert notice_text(DAY, None) == "Dienstag, 03.11.2026"


def test_states():
    window = (T(9), T(11))
    text = notice_text(DAY, window)
    assert notice_state(DAY, window, "", datetime.date(2026, 10, 1)).state == MISSING
    late = notice_state(DAY, window, "", datetime.date(2026, 10, 25))
    assert late.state == LATE and late.deadline == datetime.date(2026, 10, 20)
    assert notice_state(DAY, window, text, datetime.date(2026, 10, 25)).state == PRINTED
    # the plan moved to another day / another time: the printed notice is outdated
    assert notice_state(DAY + datetime.timedelta(days=1), window, text, DAY).state == OUTDATED
    assert notice_state(DAY, (T(13), T(15)), text, DAY).state == OUTDATED


# --- Ankündigung (how the tenants are told) --------------------------------------------------------

def test_announce_state():
    from documents.notice_rules import (A_DONE, A_OPEN, A_PRINT, A_SEND, A_TRIP, A_TRIP_PLANNED, AUSHANG, AUSHANG_HV,
                                        BRIEF, MAIL_HV, PHONE, announce_state, needs_trip)
    assert announce_state("", False, False, False) == A_OPEN
    assert announce_state(AUSHANG, False, False, False) == A_PRINT
    assert announce_state(AUSHANG, True, False, False) == A_TRIP
    assert announce_state(AUSHANG, True, False, True) == A_TRIP_PLANNED
    assert announce_state(AUSHANG, True, True, True) == A_DONE
    assert announce_state(BRIEF, False, False, False) == A_PRINT and announce_state(BRIEF, True, False, False) == A_SEND
    assert announce_state(MAIL_HV, False, False, False) == A_SEND and announce_state(PHONE, False, True, False) == A_DONE
    assert announce_state(AUSHANG_HV, True, False, False) == A_SEND
    assert needs_trip(AUSHANG) and not needs_trip(BRIEF) and not needs_trip(AUSHANG_HV)


def test_trip_hint():
    import datetime as dt
    from documents.notice_rules import trip_hint
    day = dt.date(2026, 11, 3)
    assert trip_hint(day - dt.timedelta(days=15), day) == ""
    assert "nur 5 Tage vorher" in trip_hint(day - dt.timedelta(days=5), day)
    assert "Termintag oder danach" in trip_hint(day, day) and trip_hint(None, day) == ""


def test_units_and_window():
    import datetime as dt
    from documents.notice_rules import (SOME_UNITS, WHOLE_HOUSE, effective_window, notice_text, parse_time,
                                        units_text)
    assert units_text(SOME_UNITS, " Whg 3 (Müller),  Whg 7 ") == "Nur für: Whg 3 (Müller), Whg 7"
    assert units_text(WHOLE_HOUSE, "Whg 3") == "" and units_text(SOME_UNITS, "") == ""
    assert effective_window(dt.time(8), dt.time(12), dt.time(9, 25), dt.time(10)) == ((dt.time(8), dt.time(12)), "manual")
    assert effective_window(None, None, dt.time(9, 25), dt.time(10, 5)) == ((dt.time(9), dt.time(11)), "plan")
    assert effective_window(None, None, None, None) == (None, "")
    assert parse_time("8") == dt.time(8) and parse_time("8.30") == dt.time(8, 30) and parse_time("x") is None
    assert parse_time("25:00") is None and parse_time("") is None
    assert notice_text(dt.date(2026, 11, 3), (dt.time(8), None)) == "Dienstag, 03.11.2026, ab 08:00 Uhr"


def test_page_filters():
    import datetime as dt
    from documents.notice_rules import (A_DONE, A_OPEN, A_TRIP, chosen_a_filter, chosen_horizon, count_a_filters,
                                        in_a_filter, is_late)
    assert chosen_a_filter("x") == "offen" and chosen_a_filter(A_TRIP) == A_TRIP and chosen_horizon("9") == "28"
    assert in_a_filter("offen", A_OPEN) and not in_a_filter("offen", A_DONE) and in_a_filter("alle", A_DONE)
    counts = count_a_filters([A_OPEN, A_TRIP, A_DONE])
    assert counts["offen"] == 2 and counts[A_DONE] == 1 and counts["alle"] == 3
    today = dt.date(2026, 10, 1)
    assert is_late(dt.date(2026, 9, 30), today, A_TRIP) and not is_late(dt.date(2026, 9, 30), today, A_DONE)
