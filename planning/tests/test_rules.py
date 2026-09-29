"""Business rules of tour planning. Expected values as in the prototype / spec."""

import datetime

import pytest

from planning.rules.drive_time import estimate_drive_minutes, planned_drive_minutes
from planning.rules.ordering import FAR, SHORT, order_stops
from planning.rules.working_time import TimeNotice, confirmation_problems, schedule_day, time_notice

T = datetime.time


@pytest.mark.parametrize(
    "seconds, meters, expected",
    [
        (60, 30, 0),        # same address (< 50 m)
        (61, 800, 5),       # 1:01 min -> at least 5
        (300, 4000, 5),     # exactly 5:00
        (301, 4000, 10),    # 5:01 -> ceil to 6 min -> rounded up to 10
        (1140, 15000, 20),  # 19:00 -> 20
        (1201, 15000, 25),  # 20:01 -> 21 -> 25
    ],
)
def test_tomtom_time_is_rounded_up_to_5_minutes(seconds, meters, expected):
    assert planned_drive_minutes(seconds, meters) == expected


def test_estimate_like_prototype():
    assert estimate_drive_minutes(None, (48.7, 9.0)) == 10
    assert estimate_drive_minutes((48.7, 9.0), (48.7001, 9.0001)) == 5  # < 300 m
    # about 11 km straight line -> 11 * 1.3 / 45 km/h = 19 min -> 20
    assert estimate_drive_minutes((48.68, 9.01), (48.78, 9.01)) == 20


def test_day_without_break():
    plan = schedule_day(T(8, 0), [60, 30], [15])
    assert [(s.start, s.end) for s in plan.stops] == [(T(8, 0), T(9, 0)), (T(9, 15), T(9, 45))]
    assert (plan.end, plan.net_minutes, plan.break_minutes) == (T(9, 45), 105, 0)
    assert plan.stops[0].departure == T(9, 0)


def test_break_after_first_stop_ending_after_12_when_longer_than_6_hours():
    # 8:00 + 4 stops of 90 min + 3 drives of 15 min = 405 min net (> 6 h)
    plan = schedule_day(T(8, 0), [90, 90, 90, 90], [15, 15, 15])
    # stop 1 8:00-9:30, stop 2 9:45-11:15, stop 3 11:30-13:00 -> break after stop 3
    assert plan.break_after_index == 2
    assert plan.stops[2].end == T(13, 0) and plan.stops[2].departure == T(13, 30)
    assert plan.stops[3].start == T(13, 45)
    assert (plan.end, plan.break_minutes) == (T(15, 15), 30)
    assert plan.net_minutes == 405  # the break is not working time


def test_no_break_when_day_is_6_hours_or_less():
    plan = schedule_day(T(9, 0), [120, 120, 90], [15, 15])  # 360 min exactly
    assert plan.break_after_index is None


def test_never_a_break_after_the_last_stop():
    plan = schedule_day(T(8, 0), [200, 200], [5])  # ends after 12, but only the last one
    assert plan.break_after_index is None


def test_confirmation_needs_tomtom_and_6_to_7_5_hours_or_an_approval():
    ok = schedule_day(T(8, 0), [200, 200], [15])  # 415 min net
    assert confirmation_problems(ok, all_drives_from_tomtom=True) == []
    assert time_notice(ok) is None
    assert "TomTom" in confirmation_problems(ok, all_drives_from_tomtom=False)[0]

    too_long = schedule_day(T(7, 0), [240, 240], [15])  # 495 min net
    assert time_notice(too_long) == TimeNotice("over", "Netto-Arbeitszeit 8,2 h – 0,8 h über 7,5 h")
    assert confirmation_problems(too_long, True) == [
        "Netto-Arbeitszeit 0,8 h über 7,5 h – Stopps herausnehmen oder die Arbeitszeit bewusst so übernehmen."]
    assert confirmation_problems(too_long, True, time_approved=True) == []  # the planner approved it

    too_short = schedule_day(T(8, 0), [60], [])
    assert time_notice(too_short).kind == "under" and "nicht ausgelastet" in time_notice(too_short).text
    assert "Stopps ergänzen" in confirmation_problems(too_short, True)[0]
    assert confirmation_problems(too_short, True, time_approved=True) == []


def test_order_nearest_neighbour():
    coords = {"A": (48.0, 9.0), "B": (48.5, 9.0), "C": (48.1, 9.0), "X": None}
    stops = ["A", "B", "C", "X"]
    assert order_stops(stops, SHORT, coords.get) == ["A", "C", "B", "X"]  # no coordinates -> last
    assert order_stops(stops, FAR, coords.get)[0] == "B"  # farthest from the centre first


def test_team_minutes_split_and_round_up_to_5():
    from planning.rules.working_time import team_minutes

    assert team_minutes(240, 2) == 120
    assert team_minutes(50, 3) == 20          # 16,7 -> 20
    assert team_minutes(7, 4) == 5            # never less than 5 min
    assert team_minutes(240, 1) == 240
    assert team_minutes(240, 2, split=False) == 240
    assert team_minutes(0, 2) == 0


def test_first_free_day_skips_weekends_plans_and_absences():
    from planning.rules.availability import first_free_day

    monday = datetime.date(2026, 10, 5)
    assert first_free_day(monday, set()) == monday
    assert first_free_day(datetime.date(2026, 10, 3), set()) == monday  # Saturday -> Monday
    busy = {monday, monday + datetime.timedelta(days=1)}
    assert first_free_day(monday, busy) == datetime.date(2026, 10, 7)  # Wednesday
    holiday = [(datetime.date(2026, 10, 7), datetime.date(2026, 10, 16))]
    assert first_free_day(monday, busy, holiday) == datetime.date(2026, 10, 19)
    assert first_free_day(monday, busy, [(monday, datetime.date(2027, 12, 31))], horizon=30) is None
