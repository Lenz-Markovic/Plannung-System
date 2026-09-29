"""Write a line into the Verlauf. Called by the services and views after a change."""

from .models import Activity


def record(user, kind, text, tour=None, building=None, order=None):
    """user may be None (e.g. a command); an anonymous user counts as None."""
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None
    return Activity.objects.create(user=user, kind=kind, text=text[:400], tour=tour, building=building,
                                   installation_order=order)


def who(user):
    """'Müller' / 'admin_demo' for a text."""
    if user is None:
        return "System"
    return user.get_full_name() or user.username


def day_label(date):
    """'Mo 05.10.2026' (German weekday)."""
    from django.utils.formats import date_format
    return date_format(date, "D d.m.Y")


def streets(stops, limit=3):
    """'Uhlandstraße 39, Talstraße 16 … (+3)' for a text."""
    names = []
    for stop in stops:
        target = getattr(stop, "building", None) or getattr(stop, "installation_order", None) or getattr(stop, "order", None)
        if target is not None and target.street not in names:
            names.append(target.street)
    more = f" (+{len(names) - limit})" if len(names) > limit else ""
    return ", ".join(names[:limit]) + more
