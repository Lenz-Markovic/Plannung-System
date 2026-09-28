import io

from django.core.management import call_command

from planning.tomtom import GeocodeResult, Leg, TomTomClient


def run(settings, tmp_path, env_text, monkeypatch=None):
    settings.BASE_DIR = tmp_path
    if env_text is not None:
        (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    (tmp_path / ".env.example").write_text("TOMTOM_API_KEY=\n", encoding="utf-8")
    out = io.StringIO()
    call_command("check_tomtom", stdout=out)
    return out.getvalue()


def test_missing_env_file(settings, tmp_path):
    assert "gibt es nicht" in run(settings, tmp_path, None)


def test_empty_key(settings, tmp_path):
    assert "kein TOMTOM_API_KEY" in run(settings, tmp_path, "TOMTOM_API_KEY=\n")


def test_key_in_file_but_not_loaded_needs_restart(settings, tmp_path):
    settings.TOMTOM_API_KEY = ""
    output = run(settings, tmp_path, "TOMTOM_API_KEY=abcdefgh\n")
    assert "gefunden" in output and "keinen Schlüssel geladen" in output
    assert "abcdefgh" not in output  # the key itself is never printed


def test_everything_ok(settings, tmp_path, monkeypatch):
    settings.TOMTOM_API_KEY = "abcdefgh"
    monkeypatch.setattr(TomTomClient, "geocode", lambda self, a: GeocodeResult(48.6, 8.9, a, "Point Address", ""))
    monkeypatch.setattr(TomTomClient, "route", lambda self, a, b, d: Leg(seconds=1500, meters=24000))
    output = run(settings, tmp_path, "TOMTOM_API_KEY=abcdefgh\n")
    assert "Alles in Ordnung" in output and "abcdefgh" not in output


def test_invisible_characters_are_reported(settings, tmp_path):
    settings.TOMTOM_API_KEY = "abcd​efgh"  # zero-width space that came along when copying
    output = run(settings, tmp_path, "TOMTOM_API_KEY=abcd​efgh\n")
    assert "ungewöhnliche Zeichen" in output


def test_environment_variable_hides_the_env_file(settings, tmp_path):
    """E.g. an old key in a Codespaces secret: the new key in .env is ignored."""
    settings.TOMTOM_API_KEY = "OLDoldoldold"
    output = run(settings, tmp_path, "TOMTOM_API_KEY=NEWnewnewnew\n")
    assert "ANDEREN Schlüssel" in output and "unset TOMTOM_API_KEY" in output
    assert "oldold" not in output and "newnew" not in output


def test_two_key_lines_are_reported(settings, tmp_path, monkeypatch):
    settings.TOMTOM_API_KEY = "NEWnewnewnew"
    monkeypatch.setattr(TomTomClient, "geocode", lambda self, a: GeocodeResult(48.6, 8.9, a, "Point Address", ""))
    monkeypatch.setattr(TomTomClient, "route", lambda self, a, b, d: Leg(seconds=1500, meters=24000))
    output = run(settings, tmp_path, "TOMTOM_API_KEY=OLDoldoldold\nTOMTOM_API_KEY=NEWnewnewnew\n")
    assert "2× in .env" in output
