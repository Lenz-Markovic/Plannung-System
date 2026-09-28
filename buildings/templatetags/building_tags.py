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
