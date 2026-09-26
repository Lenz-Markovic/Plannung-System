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


def extract_re_numbers(*texts):
    """Find order numbers like 'RE90298' or 're 090298' in free text.

    Port of reListe() in the prototype: used to link a building to its
    installation orders via the "Auftrag" field and handwritten notes.
    Returns a set of normalised numbers such as {"RE90298"}.
    """
    found = set()
    for text in texts:
        for match in re.findall(r"RE\s?0*\d{4,6}", str(text or ""), flags=re.IGNORECASE):
            found.add(re.sub(r"\s+", "", match).upper())
    return found
