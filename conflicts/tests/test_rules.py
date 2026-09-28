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
