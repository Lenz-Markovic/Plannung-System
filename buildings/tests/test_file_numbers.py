import pytest

from buildings.rules.file_numbers import normalize_file_number


@pytest.mark.parametrize(
    "number, expected",
    [
        ("0704806", "4806"),  # BFW Main, example from the spec
        ("70004806", "4806"),  # CEOS, same building
        ("4806", "4806"),
        ("0798615", "98615"),  # BFW number from the demo data
        ("70098611", "70098611"),  # CEOS number not starting with 7000: unchanged
        ("AZ 0704806", "4806"),  # non-digits are ignored
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_file_number_matches_prototype(number, expected):
    assert normalize_file_number(number) == expected


def test_bfw_and_ceos_number_of_same_building_match():
    assert normalize_file_number("0704806") == normalize_file_number("70004806")
