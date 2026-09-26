"""
Building numbers ("Aktenzeichen", AZ) from the source systems.

BFW Main uses 7 digits (0704806), CEOS uses 8 digits (70004806). The same
building can exist in both systems, and installation orders from Miclas
refer to buildings by number. To match them we reduce every number to a
common "core". Behaviour copied from azCore() in the HTML prototype:

    0704806  (BFW)   -> 4806
    70004806 (CEOS)  -> 4806
    4806             -> 4806
"""

import re


def normalize_file_number(number):
    """Return the comparable core of a building number ('' if there is none)."""
    digits = re.sub(r"\D", "", str(number or ""))
    if not digits:
        return ""
    if len(digits) == 8 and digits.startswith("7000"):
        return str(int(digits[4:]))
    if len(digits) == 7 and digits.startswith("07"):
        return str(int(digits[2:]))
    return str(int(digits))
