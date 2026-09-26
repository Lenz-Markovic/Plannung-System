import datetime

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction

from buildings.models import Building, SourceSystem
from planning.models import Employee, StopKind, Tour, TourStop


@pytest.fixture
def tour(db):
    user = User.objects.create_user(username="kaiser")
    employee = Employee.objects.create(user=user, short_name="Kaiser")
    return Tour.objects.create(employee=employee, date=datetime.date(2026, 12, 7))


@pytest.fixture
def building(db):
    return Building.objects.create(
        source_system=SourceSystem.BFW_MAIN, file_number="0798615", stichtag=datetime.date(2026, 12, 31)
    )


def test_only_one_tour_per_employee_and_day(tour):
    with pytest.raises(IntegrityError), transaction.atomic():
        Tour.objects.create(employee=tour.employee, date=tour.date)


def test_reading_stop_needs_a_building(tour):
    with pytest.raises(IntegrityError), transaction.atomic():
        TourStop.objects.create(tour=tour, position=1, kind=StopKind.READING)


def test_reading_stop_with_building_is_ok(tour, building):
    stop = TourStop.objects.create(tour=tour, position=1, kind=StopKind.READING, building=building)
    assert list(tour.stops.all()) == [stop]


def test_net_minutes_is_work_plus_drive(tour):
    tour.work_minutes, tour.drive_minutes = 300, 90
    assert tour.net_minutes == 390
