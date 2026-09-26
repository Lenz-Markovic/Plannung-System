"""
Estimated reading time of a building (port of calcMinutes() in the prototype).

Minutes per device, plus a surcharge for bigger buildings; the result used
for planning is rounded UP to full 10 minutes.
"""

import math
import re

MANUAL_READING_PATTERN = re.compile(r"Betreten|manuelle Ablesung|^MANU", re.IGNORECASE)


def is_manual_reading(reading_type):
    """True if the reader has to enter the apartments / read by hand."""
    return bool(MANUAL_READING_PATTERN.search(reading_type or ""))


def raw_reading_minutes(*, hkv_family, hkv_count, wmz_count, wwz_count, kwz_count, rwm_count, apartments, reading_type):
    minutes = 0.0
    if hkv_family == "Verdunster":
        minutes += hkv_count * 1  # evaporator ampoule: 1 min each
    elif hkv_family in ("Sontex 566", "Sonstige EHKV"):
        minutes += hkv_count * 0.5  # electronic HKV: 30 s each
    minutes += (wwz_count + kwz_count) * 0.5  # water meters: 30 s each
    minutes += wmz_count * 1.5  # heat meters: 1.5 min each
    minutes += rwm_count * 0.5  # smoke detectors: 30 s each

    if apartments >= 20:
        minutes += 25
    elif apartments >= 15:
        minutes += 20
    elif apartments >= 10:
        minutes += 15
    elif apartments >= 5:
        minutes += 10

    # Reset tamper protection on many Sontex 566 when reading by hand
    if is_manual_reading(reading_type) and hkv_family == "Sontex 566" and hkv_count >= 20:
        minutes += 10
    return minutes


def planned_reading_minutes(**kwargs):
    """Reading time for planning: raw minutes rounded up to full 10 minutes."""
    return math.ceil(raw_reading_minutes(**kwargs) / 10) * 10
