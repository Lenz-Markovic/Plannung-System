"""
"💶 Material & Kosten" of the Montage dashboard: which material and how much money
the installations in a time window need - to plan the budget and order in time.
Pure functions: plain data in, sums out - no database.

Three groups of orders:
  PLANNED  an installation is planned in the window (day of its Fahrplan)
  DUE      not planned yet, but must be done by the end of the window
           (latest day = 8 days before the reading of the building, like the
           conflict rule; also when that day has already passed)
  OPEN     not planned and no latest day known - the need comes some day
The total counts PLANNED + DUE; OPEN can be added with a switch.

Prices per piece: a fixed article price (ArticlePrice) wins over the price of the
category (DeviceCategory.price). Default prices are the ones of the prototype.
"""

import calendar
import datetime
from dataclasses import dataclass, field
from decimal import Decimal

DEFAULT_PRICES = {  # € per piece, from CATS in the Montage dashboard
    "EHKV": Decimal("12.00"), "FUNKMODUL": Decimal("20.00"), "GATEWAY": Decimal("180.00"),
    "STECKBL": Decimal("1.00"), "WMZ": Decimal("100.00"), "SQ1": Decimal("25.00"),
    "RWM": Decimal("25.00"), "PULS": Decimal("30.00"), "MANSCH": Decimal("3.00"), "SONST": Decimal("0.00"),
}

PLANNED, DUE, OPEN = "planned", "due", "open"
GROUPS = (PLANNED, DUE, OPEN)
GROUP_LABELS = {PLANNED: "geplant im Zeitraum", DUE: "noch ungeplant, aber bis dahin fällig", OPEN: "offen ohne Frist"}

PRESETS = {"1w": "1 Woche", "2w": "2 Wochen", "1m": "1 Monat", "3m": "3 Monate"}


def preset_window(preset, today):
    """(first day, last day) of a preset, starting today."""
    if preset == "1w":
        return today, today + datetime.timedelta(days=6)
    if preset == "2w":
        return today, today + datetime.timedelta(days=13)
    months = 3 if preset == "3m" else 1
    month = today.month - 1 + months
    year, month = today.year + month // 12, month % 12 + 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return today, datetime.date(year, month, day) - datetime.timedelta(days=1)


def order_group(planned_date, latest, start, end):
    """In which group is an order for the window start..end? None = not needed in this window."""
    if planned_date:
        return PLANNED if start <= planned_date <= end else None
    if latest is None:
        return OPEN
    return DUE if latest <= end else None


def price_for(article, category, article_prices, category_prices):
    """€ per piece: fixed article price, else the category price, else 0."""
    if article in article_prices:
        return Decimal(article_prices[article])
    return Decimal(category_prices.get(category, 0))


@dataclass(frozen=True)
class Line:
    """One item of one order."""

    order: str                 # RE number
    group: str                 # PLANNED / DUE / OPEN
    date: datetime.date | None  # planned day (PLANNED) or latest day (DUE)
    article: str
    description: str
    category: str              # category code, "" = none
    quantity: int


@dataclass
class Row:
    """One article in the material list."""

    article: str
    description: str
    category: str
    price: Decimal
    pieces: dict = field(default_factory=lambda: dict.fromkeys(GROUPS, 0))
    first_needed: datetime.date | None = None
    orders: set = field(default_factory=set)
    uses: list = field(default_factory=list)   # the Lines of this article (which order, when, how many)

    def count(self, groups):
        return sum(self.pieces[g] for g in groups)

    def money(self, groups):
        return self.price * self.count(groups)


@dataclass
class Summary:
    start: datetime.date
    end: datetime.date
    counted: tuple             # the groups in the total
    rows: list                 # Row, sorted by category and article
    orders: dict               # group -> number of orders
    money: dict                # group -> €
    pieces: dict               # group -> pieces
    by_category: list          # (category, pieces, €) of the counted groups, most expensive first
    by_week: list              # (monday, €) of PLANNED + DUE
    without_price: list        # articles in the total without a price (price 0)

    @property
    def total(self):
        return sum((self.money[g] for g in self.counted), Decimal(0))

    @property
    def total_pieces(self):
        return sum(self.pieces[g] for g in self.counted)

    @property
    def total_orders(self):
        return sum(self.orders[g] for g in self.counted)


def summarize(lines, start, end, article_prices, category_prices, with_open=False):
    counted = (PLANNED, DUE, OPEN) if with_open else (PLANNED, DUE)
    rows, orders = {}, {g: set() for g in GROUPS}
    weeks = {}
    for line in lines:
        price = price_for(line.article, line.category, article_prices, category_prices)
        row = rows.get(line.article)
        if row is None:
            row = rows[line.article] = Row(line.article, line.description, line.category, price)
        row.pieces[line.group] += line.quantity
        row.orders.add(line.order)
        row.uses.append(line)
        orders[line.group].add(line.order)
        if line.date and line.group in counted:
            needed = max(line.date, start)  # already overdue: needed at once
            row.first_needed = min(row.first_needed or needed, needed)
            monday = needed - datetime.timedelta(days=needed.weekday())
            weeks[monday] = weeks.get(monday, Decimal(0)) + price * line.quantity

    ordered = sorted(rows.values(), key=lambda r: (r.category or "~", r.article))
    for row in ordered:  # the orders of an article by date, the ones without date last
        row.uses.sort(key=lambda u: (u.date is None, u.date or start, u.order))
    categories = {}
    for row in ordered:
        pieces, money = categories.get(row.category, (0, Decimal(0)))
        categories[row.category] = (pieces + row.count(counted), money + row.money(counted))
    return Summary(
        start=start, end=end, counted=counted, rows=ordered,
        orders={g: len(orders[g]) for g in GROUPS},
        money={g: sum((r.price * r.pieces[g] for r in ordered), Decimal(0)) for g in GROUPS},
        pieces={g: sum(r.pieces[g] for r in ordered) for g in GROUPS},
        by_category=sorted(((c, p, m) for c, (p, m) in categories.items() if p), key=lambda x: -x[2]),
        by_week=sorted(weeks.items()),
        without_price=[r for r in ordered if r.count(counted) and not r.price],
    )
