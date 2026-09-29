from pathlib import Path

from django.conf import settings


def _static_version():
    """Number that changes whenever a file in static/ changes (newest modification time).

    Added to the CSS/JS links (?v=...), so the browser loads the new file after an
    update instead of an old copy from its cache.
    """
    files = [f for f in Path(settings.BASE_DIR, "static").rglob("*") if f.is_file()]
    return str(int(max((f.stat().st_mtime for f in files), default=0)))


def site(request):
    """Values every template may need."""
    return {"DEMO_BANNER": settings.DEMO_BANNER, "STATIC_VERSION": _static_version()}
