"""☰ The side menu: every page, action and quick filter as a button - only what the role may use."""

import pytest
from django.contrib.auth.models import Group, User
from django.test import Client
from django.urls import reverse

from core import roles

pytestmark = pytest.mark.django_db


def client_for(role, name):
    u = User.objects.create_user(username=name)
    u.groups.add(Group.objects.get(name=role))
    c = Client()
    c.force_login(u)
    return c


def menu(html):
    return html[html.index('<aside id="side-menu"'):html.index('id="side-menu-back"')]


def test_office_sees_pages_actions_and_quick_filters():
    html = menu(client_for(roles.DISPATCHER, "dispo").get(reverse("planning:calendar")).content.decode())
    for text in ("🏢 Liegenschaften", "🔧 Montage", "🧾 Rückmeldungen", "📅 Kalender", "⚠ Konflikte",
                 "🗺 Fahrplan erstellen", "🗓 Erste freie Tage", "📄 Alle Fahrpläne (Excel)", "seitlich öffnen",
                 "?status=rework", "?nachtermin=noetig", "?termin=nachtermin", "?konflikt=offen", "?f=probleme",
                 "🚪 Abmelden", "📌 anheften"):
        assert text in html, text
    assert "💶 Material" not in html  # Admin only


def test_admin_sees_costs_and_verwaltung():
    c = client_for(roles.ADMIN, "adm")
    User.objects.filter(username="adm").update(is_staff=True)
    html = menu(c.get(reverse("buildings:list")).content.decode())
    assert "💶 Material &amp; Kosten" in html and "⚙ Verwaltung" in html and "🕘 Verlauf" in html


def test_reader_sees_only_own_things():
    html = menu(client_for(roles.READER, "abl").get(reverse("planning:my_day"), follow=True).content.decode())
    assert "📱 Mein Tag" in html and "🚪 Abmelden" in html
    for text in ("🏢 Liegenschaften", "Rückmeldungen", "Fahrplan erstellen", "?status=", "Pop-ups"):
        assert text not in html, text


def test_calendar_opens_free_days_from_the_menu():
    js = open("static/js/calendar.js", encoding="utf-8").read()
    assert 'get("frei") === "1"' in js
