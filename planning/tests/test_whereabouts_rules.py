"""🛰 Wer ist wo? - the position by the plan (pure)."""

from planning.rules.whereabouts import (AT, BEFORE, COMMUTE, DONE, DRIVING, HOME, PAUSE, WAITING, PlanStop,
                                        behind_plan, between, clamp_time, clock, count_states, describe,
                                        where_at)

A, B, C = (48.0, 9.0), (48.2, 9.4), (48.4, 9.0)
# 08:00-08:40 at A, drive 20 min -> 09:00-09:30 at B, break 30 + drive 20 -> fixed 11:00 at C (waits 10 min)
STOPS = [
    PlanStop(1, 480, 520, 520, 20, A, "Hauptstr. 1"),
    PlanStop(2, 540, 570, 600, 20, B, "Bahnhofstr. 2"),
    PlanStop(3, 660, 720, None, None, C, "Ringstr. 3"),
]


def test_before_and_commute():
    assert where_at(STOPS, 400).state == BEFORE and where_at(STOPS, 400).point == A
    w = where_at(STOPS, 470, commute_to=25)
    assert w.state == COMMUTE and w.until == 480


def test_at_the_stop():
    w = where_at(STOPS, 500)
    assert w.state == AT and w.stop == 0 and w.until == 520 and w.point == A
    assert where_at(STOPS, 540).state == AT and where_at(STOPS, 540).stop == 1  # arriving = at the stop


def test_driving_moves_along_the_line():
    w = where_at(STOPS, 530)
    assert w.state == DRIVING and w.stop == 0 and w.next_stop == 1 and abs(w.fraction - 0.5) < 1e-9
    assert w.point == between(A, B, 0.5) == (48.1, 9.2)


def test_pause_then_drive_then_waiting_for_a_fixed_time():
    assert where_at(STOPS, 580).state == PAUSE and where_at(STOPS, 580).point == B
    assert where_at(STOPS, 610).state == DRIVING
    w = where_at(STOPS, 625)
    assert w.state == WAITING and w.stop == 2 and w.until == 660 and w.point == C


def test_after_the_last_stop():
    assert where_at(STOPS, 730, commute_from=30).state == HOME
    w = where_at(STOPS, 800)
    assert w.state == DONE and w.point == C and w.since == 720


def test_without_drive_times_the_whole_gap_is_driving():
    stops = [PlanStop(1, 480, 500, None, None, A), PlanStop(2, 540, 560, None, None, B)]
    w = where_at(stops, 520)
    assert w.state == DRIVING and abs(w.fraction - 0.5) < 1e-9


def test_missing_points_and_empty_plans():
    assert where_at([], 500) is None
    stops = [PlanStop(1, 480, 500, 500, 10, None), PlanStop(2, 510, 530, None, None, B)]
    assert where_at(stops, 505).point == B  # no point for the start: stays at the known one


def test_behind_plan_only_after_the_tolerance():
    stops = [PlanStop(1, 480, 520, 520, 20, A, reported=True), PlanStop(2, 540, 570, 570, 10, B)]
    assert behind_plan(stops, None, 590) == []
    assert behind_plan(stops, None, 600) == [2]


def test_describe_and_helpers():
    assert describe(where_at(STOPS, 500), STOPS) == "Stopp 1 · Hauptstr. 1 · bis 08:40"
    assert describe(where_at(STOPS, 530), STOPS).startswith("von Stopp 1 nach Stopp 2")
    assert "Termin um 11:00" in describe(where_at(STOPS, 625), STOPS)
    assert "fertig um 12:00" in describe(where_at(STOPS, 800), STOPS)
    assert clock(605) == "10:05" and clock(None) == ""
    assert clamp_time("10:30", 1) == 630 and clamp_time("x", 7) == 7 and clamp_time("10:99", 7) == 7
    assert count_states([AT, AT, DONE])[AT] == 2
