"""Template filters for the building list: {% load building_tags %}."""

from django import template

from buildings import display

register = template.Library()

register.filter("short_region", display.short_region)
register.filter("reading_type_code", display.reading_type_code)
register.filter("device_chips", display.device_chips)


@register.filter
def installation_short(value):
    return display.INSTALLATION_SHORT.get(value, value or "–")


@register.filter
def installation_css(value):
    return display.INSTALLATION_CSS.get(value, "")


@register.simple_tag(takes_context=True)
def status_choices(context, current):
    """Status dropdown entries with 'allowed' flags for the logged-in user."""
    from buildings.rules.status import status_options

    return status_options(context["request"].user.get_all_permissions(), current)


STATUS_ICON = {"released": "✓", "open": "●", "rework": "▲"}


@register.filter
def status_icon(value):
    return STATUS_ICON.get(value, "")
