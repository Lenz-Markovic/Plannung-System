"""Expected texts and severities as in tmFussnote() of the prototype."""

import datetime

import pytest

from conflicts.rules import CRITICAL, INFO, OK, WARNING, check_installation_vs_reading

READING = datetime.date(2026, 12, 3)


@pytest.mark.parametrize(
    "installation, severity, message",
    [
        # RE90108: installation 07.12., reading 03.12. -> 4 days AFTER
        (datetime.date(2026, 12, 7), CRITICAL, "Montage 4 Tage NACH Ablesung — Tour verschieben"),
        (READING, CRITICAL, "Montage und Ablesung am selben Tag"),
        (datetime.date(2026, 12, 2), WARNING, "nur 1 Tag Puffer vor der Ablesung"),
        (datetime.date(2026, 11, 26), WARNING, "nur 7 Tage Puffer vor der Ablesung"),
        (datetime.date(2026, 11, 25), OK, "Montage 8 Tage vor Ablesung"),
    ],
)
def test_installation_vs_reading(installation, severity, message):
    result = check_installation_vs_reading(READING, installation)
    assert (result.severity, result.message) == (severity, message)


def test_missing_dates_are_only_info():
    assert check_installation_vs_reading(READING, None).severity == INFO
    assert check_installation_vs_reading(None, READING).message == "kein Ablesetag zum Vergleich"


def test_planning_findings():
    from conflicts.rules import OtherPlan, PlannedBuilding, planning_findings

    day = datetime.date(2026, 12, 3)
    buildings = [
        PlannedBuilding(1, "4806"),
        PlannedBuilding(2, "4806"),  # same building from the other source system
        PlannedBuilding(3, "98615", other_plans=(OtherPlan("Keller", datetime.date(2027, 1, 14), False),)),
        PlannedBuilding(4, "8792", installation_dates=(datetime.date(2026, 12, 7),)),
    ]
    findings = planning_findings(buildings, day)
    assert findings[1] == []
    assert findings[2][0].message.startswith("doppelt ausgewählt")
    assert findings[3][0].moves_stop and "Keller am Do 14.01.2027 (bestätigt)" in findings[3][0].message
    assert findings[4][0].message == "Montage 07.12.2026: Montage 4 Tage NACH Ablesung — Tour verschieben"
    assert all(f[-1].message == "Mitarbeiter ist an diesem Tag abwesend" for f in planning_findings(buildings, day, absent=True).values())
