"""Guards against secrets in files that are committed to GitHub."""

from pathlib import Path

from django.conf import settings


def test_env_example_contains_no_real_values():
    """.env.example is uploaded to GitHub: the TomTom key must stay empty there."""
    lines = (Path(settings.BASE_DIR) / ".env.example").read_text(encoding="utf-8").splitlines()
    tomtom = [line for line in lines if line.startswith("TOMTOM_API_KEY=")]
    assert tomtom == ["TOMTOM_API_KEY="], "Put the real TomTom key into .env, never into .env.example"


def test_env_is_ignored_by_git():
    ignored = (Path(settings.BASE_DIR) / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignored
