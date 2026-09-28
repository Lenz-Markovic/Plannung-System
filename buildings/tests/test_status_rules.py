import pytest

from buildings.rules.status import OPEN_REWORK_PERMISSION, RELEASE_PERMISSION, can_change_status, status_options

DISPATCHER = {OPEN_REWORK_PERMISSION}
PROCESSING = {OPEN_REWORK_PERMISSION, RELEASE_PERMISSION}
READER = {"buildings.propose_status"}


@pytest.mark.parametrize(
    "permissions, current, new, allowed",
    [
        (DISPATCHER, "open", "rework", True),
        (DISPATCHER, "rework", "open", True),
        (DISPATCHER, "open", "released", False),   # only Sachbearbeitung/Admin may release
        (DISPATCHER, "released", "open", False),   # ... or take the release back
        (PROCESSING, "open", "released", True),
        (PROCESSING, "released", "rework", True),
        (READER, "open", "rework", False),         # readers can only propose
        (READER, "open", "open", True),            # "no change" is always fine
    ],
)
def test_can_change_status(permissions, current, new, allowed):
    assert can_change_status(permissions, current, new) is allowed


def test_status_options_for_dispatcher():
    assert status_options(DISPATCHER, "open") == [
        ("open", "offen", True), ("rework", "Nacharbeit", True), ("released", "freigegeben", False),
    ]
