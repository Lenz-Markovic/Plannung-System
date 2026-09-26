"""Expected values were calculated with the prototype's classify()/anlageAuto()."""

import pytest

from buildings.models import InstallationType
from buildings.rules.file_numbers import extract_re_numbers
from buildings.rules.installation_time import DEFAULT_CATEGORIES, classify_article, installation_minutes
from buildings.rules.installation_type import guess_installation_type


@pytest.mark.parametrize(
    "article, description, expected",
    [
        ("322011F", "EHKV Sontex 566 Funk", "EHKV"),
        ("333028", "Sontex Steckblende", "STECKBL"),
        ("244430F", "Funkmodul Supercom W1-R", "FUNKMODUL"),
        ("244428F", "Gateway Superlink C", "GATEWAY"),
        ("122345", "Wärmezähler", "WMZ"),
        ("211103F", "Sontex SQ1 Koax kalt", "SQ1"),
        ("222112F", "Wasserzähler", "SQ1"),
        ("999999", "Schraube", "SONST"),
    ],
)
def test_classify_article(article, description, expected):
    assert classify_article(article, description) == expected


def test_installation_minutes_re90108():
    # RE90108: 17 EHKV, 17 Steckblenden, 4 Funkmodule, 1 Gateway -> 166 min in the prototype
    minutes = {code: value for code, (_, value) in DEFAULT_CATEGORIES.items()}
    items = [("EHKV", 17), ("STECKBL", 17), ("FUNKMODUL", 4), ("GATEWAY", 1)]
    assert installation_minutes(items, minutes) == 166


@pytest.mark.parametrize(
    "reading_type, family, gateway, expected",
    [
        ("FMAN - Betreten der Wohnung, Funk parall", "Sontex 566", False, InstallationType.PARTIAL_RADIO),
        ("FAMS - Funk AMR Sontex", "Sontex 566", True, InstallationType.RADIO_GATEWAY),
        ("FSON - Funk Walk-by Sontex", "Sontex 566", False, InstallationType.RADIO),
        ("MANU - Betreten der Wohnung", "Sontex 566", False, InstallationType.SONTEX_MANUAL),
        ("MANU - Betreten der Wohnung", "Verdunster", False, InstallationType.MANUAL),
    ],
)
def test_guess_installation_type(reading_type, family, gateway, expected):
    assert guess_installation_type(reading_type, family, gateway) == expected


def test_extract_re_numbers():
    # Same as reListe(): spaces are removed, leading zeros are kept
    assert extract_re_numbers("RE90298", "Gateway offen, re 090248") == {"RE90298", "RE090248"}
    assert extract_re_numbers("-", "") == set()
