"""Automatic planning: the pure rule (planning/rules/autoplan.py)."""

import datetime

from planning.rules.autoplan import Job, Slot, plan_days

MON = datetime.date(2026, 10, 5)
TUE = MON + datetime.timedelta(days=1)
# three places close together (Nagold area) and one far away (Stuttgart)
NEAR_A, NEAR_B, NEAR_C, FAR = (48.55, 8.72), (48.56, 8.73), (48.57, 8.72), (48.78, 9.18)


def job(key, minutes, point=NEAR_A, **extra):
    return Job(key=key, kind=extra.pop("kind", "reading"), minutes=minutes, point=point, **extra)


def test_a_day_never_goes_over_7_5_hours_and_the_rest_goes_to_the_next_day():
    jobs = [job(i, 120, NEAR_A) for i in range(6)]            # 12 h of work
    proposals, left = plan_days([Slot("A", MON), Slot("A", TUE)], jobs)
    assert [p.date for p in proposals] == [MON, TUE]
    assert all(p.net <= 450 for p in proposals)
    assert sum(len(p.jobs) for p in proposals) + len(left) == 6
    assert len(proposals[0].jobs) == 3                         # 3 × 120 + drives fits, a 4th not


def test_nearest_next_stop():
    jobs = [job("far", 60, FAR), job("a", 60, NEAR_A), job("c", 60, NEAR_C), job("b", 60, NEAR_B)]
    (day,), _ = plan_days([Slot("A", MON)], jobs[1:] + jobs[:1])
    assert [j.key for j in day.jobs][:3] == ["a", "b", "c"]


def test_reader_gets_no_installation_and_windows_are_kept():
    jobs = [job("inst", 60, kind="installation"),
            job("late", 60, earliest=TUE),                      # reading only after a planned montage
            job("early", 60, latest=MON - datetime.timedelta(days=1))]  # deadline already over
    proposals, left = plan_days([Slot("reader", MON, can_read=True)], jobs)
    assert proposals == [] and {j.key for j in left} == {"inst", "late", "early"}
    proposals, _ = plan_days([Slot("reader", TUE, can_read=True)], jobs)
    assert [j.key for j in proposals[0].jobs] == ["late"]


def test_assigned_jobs_go_to_their_person_first():
    jobs = [job("free", 60), job("mine", 60, person="B")]
    proposals, _ = plan_days([Slot("A", MON), Slot("B", MON)], jobs, max_net=100)
    by_person = {p.person: [j.key for j in p.jobs] for p in proposals}
    assert by_person["B"][0] == "mine" and "mine" not in by_person.get("A", [])


def test_assigned_to_somebody_without_free_days_is_open_for_others():
    proposals, _ = plan_days([Slot("A", MON)], [job("theirs", 60, person="Z")])
    assert [j.key for j in proposals[0].jobs] == ["theirs"]


def test_a_job_longer_than_a_day_is_never_planned_automatically():
    proposals, left = plan_days([Slot("A", MON)], [job("huge", 500)])
    assert proposals == [] and [j.key for j in left] == ["huge"]


def test_reading_waits_for_the_installation_at_the_same_building():
    week = [MON + datetime.timedelta(days=i) for i in range(14)]
    slots = [Slot("both", day, can_read=True, can_install=True) for day in week if day.weekday() < 5]
    jobs = [job("read", 60, group="X"), job("inst", 60, kind="installation", group="X")]
    proposals, left = plan_days(slots, jobs)
    dates = {j.key: p.date for p in proposals for j in p.jobs}
    assert dates["inst"] == MON
    assert dates["read"] >= MON + datetime.timedelta(days=8)  # at least 8 days after the montage


def test_reading_is_not_planned_while_its_installation_cannot_be():
    # nobody can install in this period: the reading waits (it would come before the montage)
    proposals, left = plan_days([Slot("reader", MON)], [job("read", 60, group="X"), job("inst", 60, kind="installation", group="X")])
    assert proposals == [] and {j.key for j in left} == {"read", "inst"}


def test_no_drive_longer_than_one_hour_between_two_stops():
    far_away = (49.9, 10.9)  # Bamberg - far from the Nagold area
    (day,), left = plan_days([Slot("A", MON)], [job("a", 60, NEAR_A), job("bamberg", 60, far_away)])
    assert [j.key for j in day.jobs] == ["a"] and [j.key for j in left] == ["bamberg"]
