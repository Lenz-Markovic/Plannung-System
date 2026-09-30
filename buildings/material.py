"""
Database side of "💶 Material & Kosten" (the pure rules are in rules/material.py):
the order items of a time window as Lines, and the prices.
"""

import datetime
import re
from decimal import Decimal, InvalidOperation

from django.db.models import Max, Min, Q
from django.utils import timezone

from planning.models import StopKind, TourStop
from planning.queries import current_first
from planning.rules.autoplan import BUFFER_DAYS

from .models import ArticlePrice, DeviceCategory, InstallationOrder, OrderStatus
from .rules.material import Line, order_group, summarize
from .rules.file_numbers import normalize_file_number


def latest_days():
    """Building number core -> latest day for its installation (8 days before the CURRENT reading appointment:
    the next open one, e.g. a Nachtermin - not an old visited reading)."""
    current, readings = {}, {}
    for stop in current_first(TourStop.objects.filter(kind=StopKind.READING)).select_related("tour", "building"):
        current.setdefault(stop.building_id, stop)  # the first one per building is its current appointment
    for stop in current.values():
        core = normalize_file_number(stop.building.file_number)
        readings[core] = min(readings.get(core, stop.tour.date), stop.tour.date)
    return {core: day - datetime.timedelta(days=BUFFER_DAYS) for core, day in readings.items()}


def _planned_day(order):
    """The installation day that counts: the next open stop; a visited one only if it was completed."""
    if order.open_day:
        return order.open_day
    if order.visited_day and (order.last_outcome == "complete" or order.status == OrderStatus.DONE):
        return order.visited_day
    return None  # never planned, or 'teilweise / niemand da' and not planned again: still to do


def material_lines(start, end, storno=None, today=None):
    """One Line per order item that is needed in the window (see order_group).

    storno: a list that gets the RE numbers left out because of an open ⛔ Storno note.
    """
    from journal.notes import open_storno
    from planning.visits import visit_annotations

    today = today or timezone.localdate()
    latest = latest_days()
    open_stop = Q(tour_stops__kind=StopKind.INSTALLATION, tour_stops__done_at__isnull=True, tour_stops__outcome="")
    visited_stop = Q(tour_stops__kind=StopKind.INSTALLATION) & (Q(tour_stops__done_at__isnull=False)
                                                                 | Q(tour_stops__outcome__gt=""))
    orders = (InstallationOrder.objects.select_related("building")
              .annotate(open_day=Min("tour_stops__tour__date", filter=open_stop),
                        visited_day=Max("tour_stops__tour__date", filter=visited_stop))
              .annotate(**visit_annotations("installation_order"))
              .prefetch_related("items__category"))
    stornos = open_storno()[1]
    lines = []
    for order in orders:
        planned = _planned_day(order)
        if order.status == OrderStatus.DONE and (planned is None or planned >= today):
            continue  # done: nothing to buy any more (a past installation still counts for its window)
        if order.pk in stornos:
            if storno is not None:
                storno.append(order.re_number)
            continue  # ⛔ Storno: not bought
        # the building of the order decides (linked by RE number or AZ) - like the conflicts and autoplan
        core = order.building.file_number_core if order.building_id else order.building_file_number_core
        day = latest.get(core) if core else None
        group = order_group(planned, day, start, end)
        if group is None:
            continue
        place = f"{order.street}, {order.zip_code} {order.city}".strip(", ")
        for item in order.items.all():
            lines.append(Line(order.re_number, group, planned or day, item.article_number, item.description,
                              item.category.code if item.category else "", item.quantity, place))
    return lines


def prices():
    """(article prices, category prices) as {code: Decimal}."""
    return ({a.article_number: a.price for a in ArticlePrice.objects.all()},
            {c.code: c.price for c in DeviceCategory.objects.all()})


def material_summary(start, end, with_open=False):
    article_prices, category_prices = prices()
    storno = []
    summary = summarize(material_lines(start, end, storno), start, end, article_prices, category_prices, with_open)
    summary.storno = storno  # ⛔ orders left out
    return summary


def parse_price(text):
    """'12,50' / '12.50' / '' -> Decimal or None (empty). ValueError when it is not a price."""
    text = (text or "").strip().replace("€", "").replace(" ", "")
    if not text:
        return None
    shown = text
    if "," in text:
        text = text.replace(".", "").replace(",", ".")  # German: 1.250,50
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")  # "1.250" on a German page = one thousand two hundred fifty
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"„{shown}“ ist kein Preis.")
    if not value.is_finite():
        raise ValueError(f"„{shown}“ ist kein Preis.")
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
