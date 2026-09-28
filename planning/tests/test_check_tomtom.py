import io

from django.core.management import call_command

from planning.tomtom import GeocodeResult, Leg, TomTomClient, TomTomError


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


def test_key_in_env_is_used_even_if_settings_have_none(settings, tmp_path, monkeypatch):
    """The key is read fresh from .env - an empty setting (no restart) does not matter."""
    settings.TOMTOM_API_KEY = ""
    fake_tomtom(monkeypatch)
    output = run(settings, tmp_path, "TOMTOM_API_KEY=abcdefgh\n")
    assert "Alles in Ordnung" in output
    assert "abcdefgh" not in output  # the key itself is never printed


def fake_tomtom(monkeypatch):
    monkeypatch.setattr(TomTomClient, "geocode", lambda self, a: GeocodeResult(48.6, 8.9, a, "Point Address", ""))
    monkeypatch.setattr(TomTomClient, "route", lambda self, a, b, d: Leg(seconds=1500, meters=24000))
    monkeypatch.setattr(TomTomClient, "map_tile", lambda self, z, x, y: b"png")


def test_everything_ok(settings, tmp_path, monkeypatch):
    settings.TOMTOM_API_KEY = "abcdefgh"
    monkeypatch.setattr(TomTomClient, "geocode", lambda self, a: GeocodeResult(48.6, 8.9, a, "Point Address", ""))
    monkeypatch.setattr(TomTomClient, "route", lambda self, a, b, d: Leg(seconds=1500, meters=24000))
    monkeypatch.setattr(TomTomClient, "map_tile", lambda self, z, x, y: b"png")
    output = run(settings, tmp_path, "TOMTOM_API_KEY=abcdefgh\n")
    assert "Alles in Ordnung" in output and "abcdefgh" not in output


def test_invisible_characters_are_reported(settings, tmp_path):
    settings.TOMTOM_API_KEY = "abcd​efgh"  # zero-width space that came along when copying
    output = run(settings, tmp_path, "TOMTOM_API_KEY=abcd​efgh\n")
    assert "ungewöhnliche Zeichen" in output


def test_env_file_wins_over_an_old_environment_variable(settings, tmp_path, monkeypatch):
    """E.g. an old key in a Codespaces secret: the new key in .env is used anyway."""
    settings.TOMTOM_API_KEY = "OLDoldoldold"
    used = []
    monkeypatch.setattr(TomTomClient, "geocode", lambda self, a: used.append(self.api_key) or GeocodeResult(48.6, 8.9, a, "Point Address", ""))
    monkeypatch.setattr(TomTomClient, "route", lambda self, a, b, d: Leg(seconds=1500, meters=24000))
    monkeypatch.setattr(TomTomClient, "map_tile", lambda self, z, x, y: b"png")
    output = run(settings, tmp_path, "TOMTOM_API_KEY=NEWnewnewnew\n")
    assert used == ["NEWnewnewnew"] and "Alles in Ordnung" in output
    assert "oldold" not in output and "newnew" not in output


def test_two_key_lines_are_reported(settings, tmp_path, monkeypatch):
    settings.TOMTOM_API_KEY = "NEWnewnewnew"
    monkeypatch.setattr(TomTomClient, "geocode", lambda self, a: GeocodeResult(48.6, 8.9, a, "Point Address", ""))
    monkeypatch.setattr(TomTomClient, "route", lambda self, a, b, d: Leg(seconds=1500, meters=24000))
    monkeypatch.setattr(TomTomClient, "map_tile", lambda self, z, x, y: b"png")
    output = run(settings, tmp_path, "TOMTOM_API_KEY=OLDoldoldold\nTOMTOM_API_KEY=NEWnewnewnew\n")
    assert "2× in .env" in output


def test_missing_map_display_api_is_reported_but_not_fatal(settings, tmp_path, monkeypatch):
    fake_tomtom(monkeypatch)

    def no_map(self, z, x, y):
        raise TomTomError("TomTom lehnt den API-Schlüssel ab (HTTP 403)")

    monkeypatch.setattr(TomTomClient, "map_tile", no_map)
    output = run(settings, tmp_path, "TOMTOM_API_KEY=abcdefgh\n")
    assert "Map Display API" in output and "Alles in Ordnung" in output
