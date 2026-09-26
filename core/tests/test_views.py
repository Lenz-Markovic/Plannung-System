import pytest
from django.contrib.auth.models import User
from django.urls import reverse


def test_login_page_is_german(client, db):
    response = client.get(reverse("login"))
    assert response.status_code == 200
    assert "Anmelden" in response.content.decode()


def test_home_requires_login(client, db):
    response = client.get(reverse("home"))
    assert response.status_code == 302
    assert reverse("login") in response["Location"]


def test_home_after_login(client, db):
    User.objects.create_user(username="anna", password="geheim-123")
    client.login(username="anna", password="geheim-123")
    response = client.get(reverse("home"))
    assert response.status_code == 200
    assert "Das darf deine Rolle" in response.content.decode()


def test_tomtom_key_never_in_html(client, db, settings):
    settings.TOMTOM_API_KEY = "SECRET-TOMTOM-KEY"
    User.objects.create_user(username="anna", password="geheim-123")
    client.login(username="anna", password="geheim-123")
    assert "SECRET-TOMTOM-KEY" not in client.get(reverse("home")).content.decode()
