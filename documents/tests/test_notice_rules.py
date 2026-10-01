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


def test_flats_papers_and_window():
    import datetime as dt
    from documents.notice_rules import (SOME_UNITS, WHOLE_HOUSE, effective_window, notice_text, papers, parse_time,
                                        unit_list, units_text)
    assert unit_list(" Whg 3 (Müller),  Whg 7 ; Whg 9\n") == ["Whg 3 (Müller)", "Whg 7", "Whg 9"]
    assert papers(SOME_UNITS, "Whg 3, Whg 7") == ["Whg 3", "Whg 7"] and papers(WHOLE_HOUSE, "Whg 3") == [""]
    assert papers(SOME_UNITS, "") == [""]                       # no flats typed: one Aushang
    assert units_text(SOME_UNITS, "Whg 3,Whg 7") == "Nur für: Whg 3, Whg 7" and units_text(WHOLE_HOUSE, "x") == ""
    assert effective_window(dt.time(8), dt.time(12), dt.time(9, 25), dt.time(10)) == ((dt.time(8), dt.time(12)), "manual")
    assert effective_window(None, None, dt.time(9, 25), dt.time(10, 5)) == ((dt.time(9), dt.time(11)), "plan")
    assert effective_window(None, None, None, None) == (None, "")
    assert parse_time("8") == dt.time(8) and parse_time("8.30") == dt.time(8, 30) and parse_time("25:00") is None
    assert notice_text(dt.date(2026, 11, 3), (dt.time(8), None)) == "Dienstag, 03.11.2026, ab 08:00 Uhr"


def test_route_order_and_times():
    from documents.notice_rules import route_order, route_times, stop_minutes
    points = [(48.0, 9.0), (48.3, 9.0), (48.1, 9.0), None, (48.2, 9.0)]
    assert route_order(points) == [0, 2, 4, 1, 3]                         # along the line, unknown last
    assert route_order(points, start=(48.35, 9.0))[:4] == [1, 4, 2, 0]    # from the other end
    assert route_order([(48.0, 9.0)]) == [0] and route_order([None, None]) == [0, 1]
    assert stop_minutes(1, 0) == 4 and stop_minutes(0, 3) == 6
    office = (48.0, 9.0)                                                      # out and back to the office
    loop = route_order([(48.1, 9.0), (48.1, 9.1), (48.0, 9.1)], start=office, end=office)
    assert loop in ([0, 1, 2], [2, 1, 0])
    from documents.notice_rules import duration_text
    assert duration_text(95) == "1:35 h" and duration_text(35) == "35 min"
    assert route_times(480, [0, 10, 5], [5, 6, 5]) == [(480, 485), (495, 501), (506, 511)]


def test_areas_and_far_away():
    from documents.notice_rules import area_name, areas, far_away
    # two houses in Fellbach (1 km apart), a chain to Waiblingen, one far away in Leonberg, one without position
    points = [(48.81, 9.27), (48.82, 9.27), (48.83, 9.32), (48.80, 9.01), None]
    groups = areas(points)
    assert groups[0] == [0, 1, 2] and [3] in groups and [4] in groups
    assert far_away(points) == {3: round(far_away(points)[3], 1)} and far_away(points)[3] > 15
    assert far_away([(48.0, 9.0), None]) == {}
    assert area_name(["Fellbach", "Fellbach", "Waiblingen"]) == "Fellbach / Waiblingen"
    assert area_name(["A", "B", "B", "C"]) == "B / A …"
