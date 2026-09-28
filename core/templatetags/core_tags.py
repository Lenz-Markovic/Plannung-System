"""
Small template helpers used on many pages.

    {% load core_tags %}
    <a href="?{% query_with request page=2 %}">      keep all filters, change one value
    <a href="?{% sort_query request 'nr' %}">        click on a column header
    {% sort_arrow request 'nr' %}                    ▲ / ▼ for the current sort column
    {{ 80|minutes }}                                 "1h 20min"
    {{ b.remark|phone_links }}                       phone numbers become tel: links
"""

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag
def query_with(request, **changes):
    """Current query string with some values changed (None removes a value)."""
    params = request.GET.copy()
    for key, value in changes.items():
        if value is None or value == "":
            params.pop(key, None)
        else:
            params[key] = value
    if "page" not in changes:
        params.pop("page", None)  # a new filter or sort always starts on page 1
    return params.urlencode()


@register.simple_tag
def toggle_query(request, key, value):
    """Like query_with, but clicking the same value again removes it (KPI tiles)."""
    return query_with(request, **{key: None if request.GET.get(key) == str(value) else value})


@register.simple_tag
def sort_query(request, key):
    """First click sorts ascending, the second click descending."""
    current = request.GET.get("sort", "")
    return query_with(request, sort=f"-{key}" if current == key else key)


@register.simple_tag
def sort_arrow(request, key):
    current = request.GET.get("sort", "")
    if current == key:
        return "▲"
    if current == f"-{key}":
        return "▼"
    return ""


@register.filter
def minutes(value):
    """80 -> '1h 20min', 45 -> '45min' (same as fmtZeit in the prototype)."""
    total = round(value or 0)
    hours, rest = divmod(total, 60)
    return f"{hours}h {rest}min" if hours else f"{rest}min"


@register.filter
def hours(value):
    """90 -> '1,5 Std'."""
    return f"{(value or 0) / 60:.1f} Std".replace(".", ",")


@register.filter
def phone_links(text):
    """Make phone numbers in notes clickable on the phone: 'Tel. 0711/000000' -> tel: link."""
    def link(match):
        number = match.group(1)
        return f'<a href="tel:{re.sub(r"[^0-9+]", "", number)}">{number}</a>'

    return mark_safe(re.sub(r"(\+?\d[\d /\-]{5,}\d)", link, escape(text or "")))
