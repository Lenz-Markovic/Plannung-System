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
