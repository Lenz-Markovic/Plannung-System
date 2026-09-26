import datetime

import pytest

from buildings.models import Building, InstallationOrder, SourceSystem


def make_building(**kwargs):
    defaults = {
        "source_system": SourceSystem.BFW_MAIN,
        "file_number": "0704806",
        "stichtag": datetime.date(2026, 12, 31),
        "street": "Hauptstraße 8",
        "zip_code": "71034",
        "city": "Böblingen",
    }
    defaults.update(kwargs)
    return Building.objects.create(**defaults)


@pytest.mark.django_db
def test_file_number_core_is_filled_on_save():
    assert make_building().file_number_core == "4806"


@pytest.mark.django_db
def test_order_matches_building_via_core_number():
    building = make_building()
    order = InstallationOrder.objects.create(re_number="RE90108", building_file_number="70004806")
    assert order.building_file_number_core == building.file_number_core


@pytest.mark.django_db
def test_manual_reading_time_beats_calculated():
    building = make_building(reading_minutes_calculated=40)
    assert building.reading_minutes == 40
    building.reading_minutes_manual = 25
    assert building.reading_minutes == 25


@pytest.mark.django_db
def test_changes_are_recorded_in_history():
    building = make_building()
    building.status = "rework"
    building.save()
    assert building.history.count() == 2
    assert building.history.first().status == "rework"  # newest entry first
