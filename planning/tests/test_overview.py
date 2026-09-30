"""📊 Übersicht - the dashboard page."""

import datetime

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core import roles
from planning import overview, services
from planning.models import StopKind, Tour, Visit

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2026, 9, 28)


@pytest.fixture
def demo(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: TODAY)
    monkeypatch.setattr(services, "get_client", lambda: None)


def client_for(role, name):
    u = User.objects.create_user(username=name)
    u.groups.add(Group.objects.get(name=role))
    c = Client()
    c.force_login(u)
    return c


def add_visits():
    """Three reported stops of a past plan: ✓, ◐ and ✗ (kein Zugang)."""
    tour = Tour.objects.filter(stops__kind=StopKind.READING).order_by("date").first()
    day = TODAY - datetime.timedelta(days=2)
    Tour.objects.filter(employee=tour.employee, date=day).exclude(pk=tour.pk).delete()
    Tour.objects.filter(pk=tour.pk).update(date=day)
    tour.refresh_from_db()
    stops = list(tour.stops.filter(kind=StopKind.READING).order_by("position"))[:3]
    for stop, (outcome, reason, attempt) in zip(stops, [("complete", "", 1), ("partial", "", 1), ("absent", "no_access", 2)]):
        Visit.objects.create(stop=stop, building=stop.building, kind=stop.kind, date=tour.date, tour=tour,
                             people=tour.people_label, outcome=outcome, reason=reason, attempt=attempt, todo="x")
        stop.outcome = outcome
        stop.save()
    return tour, len(stops)


def test_numbers_of_the_range(demo):
    tour, n = add_visits()
    data = overview.collect("30", "", TODAY)
    assert data["reported"] == n
    totals = {key: count for key, label, count, share in data["totals"]}
    assert totals["complete"] == 1 and sum(totals.values()) == n
    day = next(c for c in data["columns"] if c["bucket"].day == tour.date)
    assert day["bucket"].total == n and all(0 < h <= 100 for _, _, _, h in day["segments"])
    if n == 3:
        assert data["reasons"][0][0] == "Kein Zugang (Schlüssel / Heizraum)"
    row = next(p for p in data["people"] if p.people == tour.people_label)
    assert row.reported >= n and row.complete == 1
    assert data["buildings_total"] == 240 and 0 <= data["released_share"] <= 100
    assert overview.collect("30", "installation", TODAY)["reported"] == 0  # only readings were reported


def test_season_is_shown_per_week(demo):
    add_visits()
    data = overview.collect("saison", "", TODAY)
    assert data["start"] <= TODAY and data["end"] == TODAY
    if (data["end"] - data["start"]).days > 45:
        assert data["per_week"] and data["columns"][0]["bucket"].label.startswith("KW")


def test_page_tiles_charts_and_table(demo):
    add_visits()
    c = client_for(roles.MANAGEMENT, "leitung")
    html = c.get(reverse("planning:overview")).content.decode()
    for text in ("📊 Übersicht", "Rückmeldungen offen", "Nachtermin nötig", "Über der 14-Tage-Frist", "Konflikte offen",
                 "Termine pro Tag", "Liegenschaften nach Status", "Montageaufträge nach Status", "Warum nicht erledigt?",
                 "Wie viele Termine bis fertig?", "Pro Person", "Als Tabelle", 'data-tip="', "chart_tips.js"):
        assert text in html, text
    part = c.get(reverse("planning:overview"), {"zeitraum": "7", "art": "reading"},
                 headers={"HX-Request": "true", "HX-Target": "ov-body"})
    body = part.content.decode()
    assert body.strip().startswith('<div id="ov-body"') and "nur Ablesung" in body and "HX-Push-Url" in part.headers
    assert c.get(reverse("planning:overview"), {"zeitraum": "kaputt", "art": "x"}).status_code == 200


def test_only_office_roles(demo):
    assert client_for(roles.READER, "abl").get(reverse("planning:overview")).status_code == 403
    html = client_for(roles.DISPATCHER, "dispo").get(reverse("planning:calendar")).content.decode()
    assert "📊 Übersicht" in html
