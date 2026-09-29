"""💶 Material & Kosten: window presets, groups, prices, sums (pure functions)."""

import datetime
from decimal import Decimal

from buildings.rules.material import (DUE, OPEN, PLANNED, Line, order_group, preset_window, price_for,
                                      summarize)

TODAY = datetime.date(2026, 9, 29)
START, END = TODAY, datetime.date(2026, 10, 12)
CATS = {"EHKV": Decimal("12"), "RWM": Decimal("25"), "SONST": Decimal("0")}


def test_presets():
    assert preset_window("1w", TODAY) == (TODAY, datetime.date(2026, 10, 5))
    assert preset_window("2w", TODAY) == (TODAY, datetime.date(2026, 10, 12))
    assert preset_window("1m", TODAY) == (TODAY, datetime.date(2026, 10, 28))
    assert preset_window("1m", datetime.date(2027, 1, 31)) == (datetime.date(2027, 1, 31), datetime.date(2027, 2, 27))


def test_groups():
    assert order_group(datetime.date(2026, 10, 1), None, START, END) == PLANNED
    assert order_group(datetime.date(2026, 11, 1), datetime.date(2026, 10, 1), START, END) is None  # planned later
    assert order_group(None, datetime.date(2026, 10, 10), START, END) == DUE
    assert order_group(None, datetime.date(2026, 9, 1), START, END) == DUE        # already overdue: needed now
    assert order_group(None, datetime.date(2026, 12, 1), START, END) is None     # later
    assert order_group(None, None, START, END) == OPEN


def test_article_price_wins_over_the_category():
    assert price_for("322011F", "EHKV", {}, CATS) == Decimal("12")
    assert price_for("322011F", "EHKV", {"322011F": Decimal("11.40")}, CATS) == Decimal("11.40")
    assert price_for("X", "", {}, CATS) == 0


def lines():
    return [
        Line("RE1", PLANNED, datetime.date(2026, 10, 1), "322011F", "EHKV", "EHKV", 30),
        Line("RE1", PLANNED, datetime.date(2026, 10, 1), "411008F", "RWM", "RWM", 10),
        Line("RE2", DUE, datetime.date(2026, 9, 20), "322011F", "EHKV", "EHKV", 5),   # overdue
        Line("RE3", OPEN, None, "411008F", "RWM", "RWM", 4),
        Line("RE3", OPEN, None, "999", "Messkopf", "SONST", 2),
    ]


def test_sum_of_planned_and_due():
    s = summarize(lines(), START, END, {}, CATS)
    assert s.total == Decimal(35 * 12 + 10 * 25) and s.total_pieces == 45 and s.total_orders == 2
    assert s.money[OPEN] == Decimal(4 * 25) and s.orders[OPEN] == 1
    ehkv = next(r for r in s.rows if r.article == "322011F")
    assert ehkv.pieces[PLANNED] == 30 and ehkv.pieces[DUE] == 5 and ehkv.first_needed == START  # overdue -> now
    assert s.by_week == [(datetime.date(2026, 9, 28), Decimal(35 * 12 + 250))]
    assert [c for c, _, _ in s.by_category] == ["EHKV", "RWM"]
    assert s.without_price == []  # the Messkopf without price is only in OPEN


def test_with_open_orders_and_missing_prices():
    s = summarize(lines(), START, END, {}, CATS, with_open=True)
    assert s.total == Decimal(35 * 12 + 14 * 25) and s.total_orders == 3
    assert [r.article for r in s.without_price] == ["999"]


def test_per_contract_table():
    s = summarize(lines(), START, END, {}, CATS)
    assert [o.order for o in s.order_rows] == ["RE2", "RE1"]  # by date; RE3 (open) is not counted
    re1 = s.order_rows[1]
    assert re1.pieces == {"EHKV": 30, "RWM": 10} and re1.total_pieces == 40 and re1.money == Decimal(30 * 12 + 10 * 25)
    assert s.order_columns == ["EHKV", "RWM"] and s.column_totals == [35, 10]
    with_open = summarize(lines(), START, END, {}, CATS, with_open=True)
    assert with_open.order_rows[-1].order == "RE3" and with_open.order_columns == ["EHKV", "RWM", "SONST"]
