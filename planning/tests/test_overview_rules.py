"""📊 Übersicht - the numbers (pure)."""

import datetime

from planning.rules.overview import (ABSENT, COMPLETE, PARTIAL, attempts_needed, buckets, chosen_period,
                                     count_reasons, nice_max, per_person, percent, period_range,
                                     share_segments, visits_per_bucket)

TODAY = datetime.date(2026, 9, 30)  # a Wednesday


def test_periods():
    assert chosen_period("7") == "7" and chosen_period("x") == "30"
    assert period_range("7", TODAY) == (datetime.date(2026, 9, 24), TODAY)
    assert period_range("saison", TODAY, datetime.date(2026, 6, 1)) == (datetime.date(2026, 6, 1), TODAY)
    assert period_range("saison", TODAY, datetime.date(2027, 1, 1))[0] == TODAY  # never after today


def test_days_or_weeks():
    days, per_week = buckets(TODAY - datetime.timedelta(days=6), TODAY)
    assert len(days) == 7 and not per_week
    weeks, per_week = buckets(TODAY - datetime.timedelta(days=89), TODAY)
    assert per_week and all(d.weekday() == 0 for d in weeks)


def test_visits_per_day_skip_empty_weekends():
    start = datetime.date(2026, 9, 24)  # Thu .. Wed
    visits = [(datetime.date(2026, 9, 24), COMPLETE), (datetime.date(2026, 9, 24), ABSENT),
              (datetime.date(2026, 9, 27), PARTIAL), (datetime.date(2026, 1, 1), COMPLETE)]
    found = visits_per_bucket(start, TODAY, visits)
    days = [b.day for b in found]
    assert datetime.date(2026, 9, 26) not in days          # empty Saturday left out
    assert datetime.date(2026, 9, 27) in days              # Sunday with a visit stays
    first = found[0]
    assert (first.complete, first.absent, first.total, first.label) == (1, 1, 2, "24.09.")


def test_visits_per_week():
    start = TODAY - datetime.timedelta(days=89)
    found = visits_per_bucket(start, TODAY, [(TODAY, COMPLETE), (TODAY - datetime.timedelta(days=1), PARTIAL)])
    last = found[-1]
    assert last.per_week and last.total == 2 and last.label.startswith("KW ")


def test_nice_axis():
    assert nice_max(37) == (40, [0, 10, 20, 30, 40])
    assert nice_max(0) == (1, [0, 1])
    top, ticks = nice_max(3)
    assert top >= 3 and ticks[0] == 0 and ticks[-1] == top
    top, ticks = nice_max(1234)
    assert top == 1500 and ticks == [0, 500, 1000, 1500]


def test_attempts_reasons_percent():
    assert attempts_needed([1, 1, 2, 3, 5]) == {"1.": 2, "2.": 1, "3. +": 2}
    labels = {"absent": "Niemand angetroffen", "no_access": "Kein Zugang"}
    assert count_reasons(["absent", "no_access", "absent", ""], labels) == [
        ("Niemand angetroffen", 2), ("Kein Zugang", 1), ("ohne Grund", 1)]
    assert percent(1, 3) == 33 and percent(1, 0) == 0


def test_per_person():
    rows = per_person([("Keller", True), ("Keller", False), ("Kaiser", True)],
                      [("Keller", COMPLETE), ("Kaiser", ABSENT), ("Neu", PARTIAL)])
    keller = rows[0]
    assert keller.people == "Keller" and keller.planned == 2 and keller.open == 1 and keller.reported_share == 50
    assert keller.success == 100 and rows[-1].people == "Neu" and rows[-1].planned == 0


def test_share_segments():
    parts = share_segments([("open", "offen", 3), ("rework", "Nacharbeit", 0), ("released", "frei", 1)])
    assert [p[0] for p in parts] == ["open", "released"] and parts[0][3] == 75
    assert share_segments([("a", "a", 0)]) == []
