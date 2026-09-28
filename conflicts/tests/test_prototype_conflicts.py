"""The full conflict check gives exactly the messages of the prototype.

prototype_messages.json was written by running the prototype page
(docs/prototype/DEMO_Deckblaetter_Dashboard.html) in a browser and saving
bewerteMontage()'s messages for all 240 buildings.
"""

import io
import json
from pathlib import Path

import pytest
from django.core.management import call_command

from buildings.models import Building
from conflicts import services

pytestmark = pytest.mark.django_db
SEVERITY = {"krit": "critical", "warn": "warning", "info": "info", "ok": "ok"}
SOURCE = {"BFW": "bfw_main", "CEOS": "ceos"}


@pytest.fixture
def expected():
    rows = json.loads((Path(__file__).parent / "data" / "prototype_messages.json").read_text(encoding="utf-8"))
    return {(SOURCE.get(r["quelle"], r["quelle"]), r["nr"]): [(SEVERITY[s], re, via, text) for s, re, via, text in r["msgs"]]
            for r in rows}


def test_messages_are_the_same_as_in_the_prototype(expected):
    call_command("import_prototype", stdout=io.StringIO())
    result = services.evaluate(Building.objects.all())
    ours = {(b.source_system, b.file_number): [(m.severity, m.re_number, m.via, m.text) for m, *_ in items]
            for b, items in result.items()}
    assert set(ours) == set(expected)
    differences = {key: (ours[key], expected[key]) for key in expected if sorted(ours[key]) != sorted(expected[key])}
    assert not differences, json.dumps({str(k): v for k, v in list(differences.items())[:3]}, ensure_ascii=False, indent=1)


# --- storing in the Conflict table -------------------------------------------------

import datetime  # noqa: E402

from django.contrib.auth.models import Group, User  # noqa: E402
from django.core.exceptions import PermissionDenied, ValidationError  # noqa: E402

from conflicts.models import Conflict  # noqa: E402
from core import roles  # noqa: E402
from planning.models import StopKind, TourStop  # noqa: E402


@pytest.fixture
def imported():
    call_command("import_prototype", stdout=io.StringIO())
    services.refresh_conflicts()


def test_import_stores_the_conflicts_without_ok_messages(imported, expected):
    not_ok = sum(1 for messages in expected.values() for m in messages if m[0] != "ok")
    assert Conflict.objects.count() == not_ok == 29
    assert Conflict.objects.filter(severity="critical").count() == 4


def dispatcher():
    user = User.objects.create_user(username="dispo")
    user.groups.add(Group.objects.get(name=roles.DISPATCHER))
    return user


def test_acknowledged_conflict_survives_a_refresh(imported):
    conflict = Conflict.objects.filter(severity="critical").first()
    with pytest.raises(ValidationError):
        services.acknowledge(conflict, dispatcher(), "  ")
    services.acknowledge(conflict, User.objects.get(username="dispo"), "Mieter nur an dem Tag da")
    services.refresh_conflicts()
    conflict.refresh_from_db()
    assert conflict.acknowledged_note == "Mieter nur an dem Tag da"


def test_reader_may_not_acknowledge(imported):
    reader = User.objects.create_user(username="abl")
    reader.groups.add(Group.objects.get(name=roles.READER))
    with pytest.raises(PermissionDenied):
        services.acknowledge(Conflict.objects.first(), reader, "egal")


def free_day(tour, day, step):
    """The next day (in direction `step`) on which the person has no other tour."""
    while tour.employee.tours.filter(date=day).exclude(pk=tour.pk).exists():
        day += datetime.timedelta(days=step)
    return day


def test_moving_the_installation_solves_the_conflict_and_changes_reset_the_note(imported):
    conflict = Conflict.objects.filter(rule="install_after_reading").select_related("stop__tour", "other_stop__tour").first()
    services.acknowledge(conflict, dispatcher(), "geht so")
    reading_day = conflict.other_stop.tour.date
    tour = conflict.stop.tour

    # a few days later, still after the reading: new text -> the old note no longer fits
    tour.date = free_day(tour, tour.date + datetime.timedelta(days=1), +1)
    tour.save()
    services.refresh_for(order_ids=[conflict.installation_order_id])
    conflict.refresh_from_db()
    assert conflict.acknowledged_at is None and "NACH" in conflict.message

    # two weeks before the reading: no conflict any more -> row deleted
    tour.date = free_day(tour, reading_day - datetime.timedelta(days=14), -1)
    tour.save()
    services.refresh_for(order_ids=[conflict.installation_order_id])
    assert not Conflict.objects.filter(pk=conflict.pk).exists()


def test_refresh_for_finds_the_buildings_of_an_order(imported):
    stop = TourStop.objects.filter(kind=StopKind.INSTALLATION, installation_order__building__isnull=False).first()
    buildings = services.buildings_for(order_ids=[stop.installation_order_id])
    assert stop.installation_order.building in buildings


def test_update_conflicts_command(imported):
    Conflict.objects.all().delete()
    out = io.StringIO()
    call_command("update_conflicts", stdout=out)
    assert "29 Meldungen gespeichert, davon 4 offene Konflikte" in out.getvalue()
