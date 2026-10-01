"""python manage.py demo_vorbereiten: a fresh demo with examples on every page (run twice = the same again)."""

import datetime
import io

import pytest
from django.core.management import call_command
from django.utils import timezone

from journal.models import Note
from planning import services
from planning.models import Employee, StopKind, Tour, TourStop, Visit

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2026, 10, 1)


def test_fresh_demo_with_examples(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: TODAY)
    monkeypatch.setattr(services, "get_client", lambda: None)
    for _ in range(2):  # again: deletes and starts over, no duplicates
        out = io.StringIO()
        call_command("demo_vorbereiten", password="Demo-Passwort-lang-2026", stdout=out)
    assert "Fertig" in out.getvalue()
    first = Tour.objects.filter(stops__kind=StopKind.READING).order_by("date").first().date
    assert TODAY - datetime.timedelta(days=11) <= first <= TODAY - datetime.timedelta(days=3)  # about a week ago
    assert Tour.objects.filter(employee=Employee.objects.get(short_name="Demo-Ableser"), date=TODAY).exists()
    assert Visit.objects.filter(outcome="partial").exists() and Visit.objects.filter(outcome="absent").exists()
    assert TourStop.objects.filter(notice_wanted=True, notice_printed_at__isnull=True).exists()
    assert TourStop.objects.filter(notice_printed_at__isnull=False).exists()
    assert TourStop.objects.filter(notice_scope="wohnungen").count() == 1 and Note.objects.count() == 3
