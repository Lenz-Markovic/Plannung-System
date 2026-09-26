"""First guess of the 'Anlagenstatus' (port of anlageAuto() in the prototype).

Used only to pre-fill the field; after that users may change it freely.
"""

import re

from buildings.models import InstallationType


def guess_installation_type(reading_type, hkv_family, has_gateway):
    reading_type = reading_type or ""
    is_radio = bool(re.search(r"^F|Funk", reading_type, re.IGNORECASE))
    is_partial_radio = bool(re.search(r"Teilfunk|Funk parall", reading_type, re.IGNORECASE))
    if is_partial_radio:
        return InstallationType.PARTIAL_RADIO
    if is_radio and has_gateway:
        return InstallationType.RADIO_GATEWAY
    if is_radio:
        return InstallationType.RADIO
    if hkv_family == "Sontex 566":
        return InstallationType.SONTEX_MANUAL
    return InstallationType.MANUAL
