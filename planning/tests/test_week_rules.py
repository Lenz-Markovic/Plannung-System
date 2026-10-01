"""🗓 Wochenplanung by hand - the week, cells, moving items (pure)."""

import datetime

from planning.rules.week import (ABSENT, DRAFT, FREE, PLANNED, TEAM, can_take, cell_state, chosen_monday, day_load,
                                 default_monday, hours, item_key, parse_item, place, pool_sort_key, remove, totals,
                                 week_days, week_label, window_hint)

MON = datetime.date(2026, 10, 5)
R1, R2, M1 = {"kind": "reading", "building": 1}, {"kind": "reading", "building": 2}, {"kind": "installation", "order": 1}


def test_week():
    assert default_monday(datetime.date(2026, 10, 7)) == MON                       # Wednesday: this week
    assert default_monday(datetime.date(2026, 10, 2)) == MON                       # Friday: the next week
    assert chosen_monday("2026-10-08", MON) == MON and chosen_monday("x", datetime.date(2026, 10, 6)) == MON
    assert week_days(MON)[-1] == datetime.date(2026, 10, 9)
    assert week_label(MON) == "KW 41 · 05.10. – 09.10.2026"


def test_items():
    assert parse_item("reading:12") == {"kind": "reading", "building": 12}
    assert parse_item("installation:7") == {"kind": "installation", "order": 7}
    assert parse_item("help:1") is None and parse_item("reading:x") is None and parse_item(None) is None
    assert item_key(R1) != item_key(M1)  # building 1 and order 1 are different objects
    assert can_take("reading", True, False) and not can_take("installation", True, False)


def test_cells_never_overwrite_plans_or_absences():
    assert cell_state(True, True, False, True) == ABSENT
    assert cell_state(False, True, False, True) == PLANNED
    assert cell_state(False, False, True, False) == TEAM
    assert cell_state(False, False, False, True) == DRAFT and cell_state(False, False, False, False) == FREE


def test_place_moves_and_never_duplicates():
    days, moved = place([], 1, "2026-10-05", [R1, R2])
    assert len(days) == 1 and len(days[0]["stops"]) == 2 and not moved
    days, moved = place(days, 1, "2026-10-05", [R1])
    assert len(days[0]["stops"]) == 2                                              # already there
    days, moved = place(days, 2, "2026-10-06", [R1])                               # moves to another person/day
    assert moved == {(1, "2026-10-05")}
    assert [item_key(s) for s in days[0]["stops"]] == [item_key(R2)] and days[1]["stops"] == [R1]
    days, _ = place(days, 2, "2026-10-06", [R2])                                   # the last item leaves: day gone
    assert len(days) == 1 and len(days[0]["stops"]) == 2


def test_remove():
    days, _ = place([], 1, "2026-10-05", [R1, M1])
    days = remove(days, 1, "2026-10-05", R1)
    assert days[0]["stops"] == [M1]
    assert remove(days, 1, "2026-10-05", M1) == [] and remove(days, 1, "2026-10-05") == []


def test_load_hints_totals():
    assert day_load(300, 40) == (340, "under") and day_load(400, 40) == (440, "ok")
    assert day_load(420, 60, max_minutes=450) == (480, "over")
    assert hours(390) == "6,5 h"
    assert "frühestens am 12.10." in window_hint(MON, earliest=datetime.date(2026, 10, 12))
    assert "spätestens" in window_hint(MON, latest=datetime.date(2026, 10, 1)) and window_hint(MON) == ""
    days, _ = place([], 1, "2026-10-05", [R1, R2])
    days[0]["work"], days[0]["drive"] = 100, 20
    assert totals(days) == {"days": 1, "stops": 2, "minutes": 120}


def test_pool_order():
    items = [{"zip": "7", "street": "b"}, {"revisit": True, "zip": "9"}, {"ticked": True, "zip": "9"},
             {"latest": datetime.date(2026, 10, 1), "zip": "8"}]
    ordered = sorted(items, key=pool_sort_key)
    assert ordered[0].get("ticked") and ordered[1].get("revisit") and ordered[2].get("latest") and ordered[3]["zip"] == "7"


def test_notice_items():
    from planning.rules.week import NOTICE, item_value
    n1, n2 = parse_item("notice:b12"), parse_item("notice:o7")
    assert n1 == {"kind": NOTICE, "building": 12, "order": None} and n2 == {"kind": NOTICE, "building": None, "order": 7}
    assert item_value(n1) == "notice:b12" and item_value(n2) == "notice:o7"
    assert parse_item("notice:x1") is None and parse_item("notice:b") is None
    assert item_key(n1) != item_key(R1) and item_key(n1) != item_key(parse_item("notice:o12"))
    assert can_take(NOTICE, True, True, can_notice=False) is False and can_take(NOTICE, False, False, can_notice=True)
    days, _ = place([], 1, "2026-10-05", [n1, R1])
    assert len(days[0]["stops"]) == 2
