"""
Database side of "💶 Material & Kosten" (the pure rules are in rules/material.py):
the order items of a time window as Lines, and the prices.
"""

import datetime
from decimal import Decimal, InvalidOperation

from django.db.models import Min, Q

from planning.models import StopKind, TourStop
from planning.rules.autoplan import BUFFER_DAYS

from .models import ArticlePrice, DeviceCategory, InstallationOrder, OrderStatus
from .rules.material import Line, order_group, summarize
from .rules.file_numbers import normalize_file_number


def latest_days():
    """Building number core -> latest day for its installation (8 days before the first planned reading)."""
    readings = {}
    for stop in TourStop.objects.filter(kind=StopKind.READING).select_related("tour", "building"):
        core = normalize_file_number(stop.building.file_number)
        readings[core] = min(readings.get(core, stop.tour.date), stop.tour.date)
    return {core: day - datetime.timedelta(days=BUFFER_DAYS) for core, day in readings.items()}


def material_lines(start, end):
    """One Line per order item that is needed in the window (see order_group)."""
    latest = latest_days()
    orders = (InstallationOrder.objects
              .annotate(planned=Min("tour_stops__tour__date", filter=Q(tour_stops__kind=StopKind.INSTALLATION)))
              .prefetch_related("items__category"))
    lines = []
    for order in orders:
        if order.planned is None and order.status == OrderStatus.DONE:
            continue  # done without a plan in the system: nothing to buy any more
        day = latest.get(order.building_file_number_core) if order.building_file_number_core else None
        group = order_group(order.planned, day, start, end)
        if group is None:
            continue
        place = f"{order.street}, {order.zip_code} {order.city}".strip(", ")
        for item in order.items.all():
            lines.append(Line(order.re_number, group, order.planned or day, item.article_number, item.description,
                              item.category.code if item.category else "", item.quantity, place))
    return lines


def prices():
    """(article prices, category prices) as {code: Decimal}."""
    return ({a.article_number: a.price for a in ArticlePrice.objects.all()},
            {c.code: c.price for c in DeviceCategory.objects.all()})


def material_summary(start, end, with_open=False):
    article_prices, category_prices = prices()
    return summarize(material_lines(start, end), start, end, article_prices, category_prices, with_open)


def parse_price(text):
    """'12,50' / '12.50' / '' -> Decimal or None (empty). ValueError when it is not a price."""
    text = (text or "").strip().replace("€", "").replace(" ", "")
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")  # German: 1.250,50
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"„{text}“ ist kein Preis.")
    if value < 0 or value > Decimal("9999999"):
        raise ValueError("Der Preis muss zwischen 0 und 9.999.999 € liegen.")
    return value.quantize(Decimal("0.01"))


def set_category_price(code, text):
    category = DeviceCategory.objects.get(code=code)
    category.price = parse_price(text) or Decimal(0)
    category.save(update_fields=["price"])
    return category


def set_article_price(article, description, text):
    """A fixed price for one article; empty = back to the category price."""
    value = parse_price(text)
    if value is None:
        ArticlePrice.objects.filter(article_number=article).delete()
        return None
    price, _ = ArticlePrice.objects.update_or_create(
        article_number=article, defaults={"price": value, "description": description[:200]})
    return price
