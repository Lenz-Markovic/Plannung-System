import datetime
import io
import json

import pytest
from django.core.management import call_command

from buildings.importers.prototype import extract_constant, js_literal_to_json
from buildings.models import Building, BuildingStatus, InstallationOrder
from documents.models import CostDocumentReceipt
from planning.models import Employee, RoutingSource, Tour, TourStop


def test_js_literal_to_json():
    js = """[{re:"RE1", items:[ ["322011F", "EHKV: Funk, 17 Stück", 17], ], ok:true, x:null},]"""
    assert json.loads(js_literal_to_json(js)) == [
        {"re": "RE1", "items": [["322011F", "EHKV: Funk, 17 Stück", 17]], "ok": True, "x": None}
    ]


def test_js_single_quoted_strings():
    assert extract_constant("const A = ['Kaiser','Schäfer'];", "A") == ["Kaiser", "Schäfer"]


@pytest.fixture
def imported(db):
    """Run the real import (each test gets a fresh, empty test database)."""
    call_command("import_prototype", stdout=io.StringIO())


def test_counts(imported):
    assert Building.objects.count() == 240
    assert InstallationOrder.objects.count() == 70
    assert InstallationOrder.objects.filter(building__isnull=False).count() == 28
    assert Employee.objects.filter(can_read=True).count() == 11
    assert Employee.objects.filter(can_install=True).count() == 7
    assert CostDocumentReceipt.objects.count() == 19


def test_building_fields(imported):
    building = Building.objects.get(file_number="0798615")
    assert building.source_system == "bfw_main"
    assert building.file_number_core == "98615"
    assert building.stichtag == datetime.date(2026, 12, 31)
    assert building.billing_period_start == datetime.date(2026, 1, 1)
    assert building.street == "Mörikeweg 8"
    assert building.hkv_count == 50
    assert building.has_rwm is True
    assert building.cover_sheet.page == 1


def test_order_time_from_prototype_is_kept_as_manual_value(imported):
    order = InstallationOrder.objects.get(re_number="RE90108")
    assert order.duration_minutes_calculated == 166  # from the contract lines
    assert order.duration_minutes == 256  # value from the prototype's Fahrplan
    assert order.items.count() == 4
    assert list(order.assigned_installers.values_list("short_name", flat=True)) == ["Kaiser"]


def test_planned_days_become_provisional_tours_without_drive_times(imported):
    assert TourStop.objects.filter(kind="reading").count() == 167
    assert TourStop.objects.filter(kind="installation").count() == 42
    assert not Tour.objects.exclude(routing_source=RoutingSource.NONE).exists()
    assert not Tour.objects.exclude(status="provisional").exists()


def test_import_twice_does_not_duplicate(imported):
    call_command("import_prototype", stdout=io.StringIO())
    assert Building.objects.count() == 240
    assert TourStop.objects.count() == 209


def test_import_is_in_history(imported):
    building = Building.objects.filter(status=BuildingStatus.RELEASED).first()
    assert building.history.first().history_change_reason == "Import aus HTML-Prototyp"
