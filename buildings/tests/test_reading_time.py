"""Expected values were calculated with the prototype's own calcMinutes()."""

import pytest

from buildings.rules.reading_time import is_manual_reading, planned_reading_minutes


def minutes(family, hkv, wmz, wwz, kwz, rwm, apartments, reading_type):
    return planned_reading_minutes(
        hkv_family=family, hkv_count=hkv, wmz_count=wmz, wwz_count=wwz, kwz_count=kwz,
        rwm_count=rwm, apartments=apartments, reading_type=reading_type,
    )


@pytest.mark.parametrize(
    "building, expected",
    [
        # (family, hkv, wmz, wwz, kwz, rwm, apartments, reading type) from demo buildings
        (("Verdunster", 85, 6, 21, 27, 0, 16, "MANU - Betreten der Wohnung"), 140),  # 70098022
        (("Unbekannt/nur Zaehler", 0, 3, 2, 4, 0, 2, "Verdunster, manuelle Ablesung"), 10),  # 0798327
        (("Verdunster", 69, 0, 0, 25, 0, 16, "MANU - Betreten der Wohnung"), 110),  # 0798585
        (("Sontex 566", 83, 0, 22, 35, 0, 12, "FMAN - Betreten der Wohnung, Funk parall"), 100),  # 0798435
        (("Sontex 566", 36, 1, 6, 7, 20, 6, "FAMS - Funk AMR Sontex"), 50),  # 0798559
        (("Sontex 566", 50, 0, 8, 9, 0, 8, "FMAN - Betreten der Wohnung, Funk parall"), 60),  # 0798995
    ],
)
def test_matches_prototype(building, expected):
    assert minutes(*building) == expected


def test_rounds_up_to_full_ten_minutes():
    # 1 heat meter = 1.5 min -> 10 min
    assert minutes("Unbekannt/nur Zaehler", 0, 1, 0, 0, 0, 1, "") == 10


def test_tamper_reset_only_for_manual_sontex_with_20_or_more():
    radio = minutes("Sontex 566", 20, 0, 0, 0, 0, 1, "FAMS - Funk AMR Sontex")
    manual = minutes("Sontex 566", 20, 0, 0, 0, 0, 1, "MANU - Betreten der Wohnung")
    assert (radio, manual) == (10, 20)


@pytest.mark.parametrize(
    "reading_type, expected",
    [
        ("MANU - Betreten der Wohnung", True),
        ("Verdunster, manuelle Ablesung", True),
        ("FSON - Funk Walk-by Sontex", False),
        ("", False),
    ],
)
def test_is_manual_reading(reading_type, expected):
    assert is_manual_reading(reading_type) is expected
