"""
Installation orders: article categories and installation time.

Ports of classify() and berechneteZeit() from the Montage dashboard.
The installation time is the sum of quantity x minutes per piece of the
article's category (minutes are editable in DeviceCategory).
"""

import re

# Default categories (CATS in the Montage dashboard): code -> (label, minutes per piece).
# Prices: see DEFAULT_PRICES in buildings/rules/material.py (only for the Admin budget view).
DEFAULT_CATEGORIES = {
    "EHKV": ("EHKV (322011F)", 8),
    "FUNKMODUL": ("Funkmodul W1-R (244430F)", 0),
    "GATEWAY": ("Gateway Superlink C (244428F)", 30),
    "STECKBL": ("Steckblende (333028)", 0),
    "WMZ": ("Wärmezähler / WMZ (122xxx)", 10),
    "SQ1": ("Wasserzähler SQ1", 8),
    "RWM": ("Rauchwarnmelder (411008F)", 5),
    "PULS": ("Funk-Pulsadapter (155048F)", 0),
    "MANSCH": ("Manschette/Rosette (244025)", 0),
    "SONST": ("Sonstiges / Kleinteile", 0),
}

FIXED_ARTICLES = {
    "322011F": "EHKV",
    "244430F": "FUNKMODUL",
    "244428F": "GATEWAY",
    "333028": "STECKBL",
    "411008F": "RWM",
    "155048F": "PULS",
    "244025": "MANSCH",
}


def classify_article(article_number, description):
    """Return the category code of an order line, e.g. 'EHKV' or 'SQ1'."""
    article = article_number or ""
    description = description or ""
    if article in FIXED_ARTICLES:
        return FIXED_ARTICLES[article]
    if article.startswith("122"):
        return "WMZ"
    is_sq1 = re.search(r"SQ1", description, re.IGNORECASE)
    if re.fullmatch(r"211(0[1-9]|1[0-2])\d?F?", article) and is_sq1:
        return "SQ1"
    if re.fullmatch(r"222(11[1-6])F", article):
        return "SQ1"
    if is_sq1:
        return "SQ1"
    return "SONST"


def installation_minutes(items, minutes_per_category):
    """Sum of quantity x minutes per piece.

    items: list of (category_code, quantity)
    minutes_per_category: dict category_code -> minutes (unknown -> 0)
    """
    return sum(quantity * minutes_per_category.get(category, 0) for category, quantity in items)
