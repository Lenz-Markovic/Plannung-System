"""Gateway check (pure): Funk + Gateway Liegenschaften are read by the gateway, not by a visit.

How the office works:
1. The office checks the gateway: how many radio devices were received.
2. 100 % received (and the values of devices without radio sent in) -> freigeben, no appointment.
3. Devices missing -> first a try FROM OUTSIDE, without an appointment and without an Aushang,
   only for the missing devices (a few minutes, not the whole readout).
4. Still missing after that -> a normal appointment (Nachtermin) for those devices.
5. Devices without radio in a gateway building: the values are sent to us (nobody drives there for them).
"""

import math
from dataclasses import dataclass

# states
NOT_GATEWAY = ""
CHECK = "pruefen"            # not checked yet
GAP = "luecke"               # devices missing -> try from outside
PLANNED = "geplant"          # the try from outside / the appointment is planned
APPOINTMENT = "termin"       # outside did not catch everything -> appointment
WAIT_VALUES = "werte"        # radio complete, values of the devices without radio still to come
COMPLETE = "vollstaendig"    # everything there -> freigeben
RELEASED = "freigegeben"

LABELS = {
    CHECK: "📡 Gateway prüfen",
    GAP: "📡 Lücke – von außen versuchen (ohne Termin)",
    PLANNED: "🚗 geplant",
    APPOINTMENT: "📅 von außen nicht alles – Termin nötig",
    WAIT_VALUES: "✉ Werte der Geräte ohne Funk angefordert",
    COMPLETE: "✓ 100 % – freigeben",
    RELEASED: "✓ freigegeben",
}
ORDER = [GAP, APPOINTMENT, CHECK, WAIT_VALUES, COMPLETE, PLANNED, RELEASED]   # what needs work first

MANUAL_NONE, MANUAL_REQUESTED, MANUAL_RECEIVED = "", "angefordert", "erhalten"
MANUAL_STATES = [(MANUAL_NONE, "–"), (MANUAL_REQUESTED, "angefordert"), (MANUAL_RECEIVED, "erhalten")]

OUTSIDE = "aussen"           # TourStop.visit_mode: try from outside, no appointment
BASE_MINUTES = 10            # walking around the house with the radio receiver
MINUTES_PER_DEVICE = 0.5


def is_gateway(installation_type, has_gateway):
    return bool(has_gateway) or installation_type == "radio_gateway"


def radio_devices(hkv, wmz, wwz, kwz):
    """Suggestion for "Funk-Geräte gesamt" (the office corrects it)."""
    return hkv + wmz + wwz + kwz


def missing(total, received):
    if total is None or received is None:
        return 0
    return max(total - received, 0)


def gap_minutes(missing_devices):
    """Minutes for the missing devices only, rounded up to 10."""
    return math.ceil((BASE_MINUTES + missing_devices * MINUTES_PER_DEVICE) / 10) * 10


@dataclass(frozen=True)
class GatewayFacts:
    checked: bool
    total: int | None
    received: int | None
    manual_devices: int = 0
    manual_state: str = MANUAL_NONE
    last_visit: str = ""          # outcome of the last visit after the check: complete / partial / absent / ""
    planned: bool = False         # an open stop is planned (outside try or appointment)
    released: bool = False


def gateway_state(f):
    if f.released:
        return RELEASED
    if not f.checked:
        return CHECK
    if missing(f.total, f.received) and f.last_visit != "complete":
        if f.planned:
            return PLANNED
        if f.last_visit in ("partial", "absent"):
            return APPOINTMENT
        return GAP
    if f.manual_devices and f.manual_state != MANUAL_RECEIVED:
        return WAIT_VALUES
    return COMPLETE


def needs_visit(state):
    """Only these go into planning; every other gateway building needs no appointment."""
    return state in (GAP, APPOINTMENT)


def check_problems(total, received, manual_devices):
    problems = []
    if total is None or received is None:
        problems.append("Bitte „empfangen“ und „gesamt“ eintragen.")
    elif received > total:
        problems.append("Es können nicht mehr Geräte empfangen sein als es gibt.")
    if manual_devices is not None and manual_devices < 0:
        problems.append("Anzahl ohne Funk darf nicht negativ sein.")
    return problems
