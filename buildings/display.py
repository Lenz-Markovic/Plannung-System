"""
How a building is shown in the list (same look as the Deckblätter prototype).

Only presentation: short labels, device chips and the combined
"Ablesung und Montage" column. No business decisions are made here; the
installation-vs-reading check comes from conflicts/rules.py.
"""

import re
from dataclasses import dataclass, field

from conflicts.rules import CRITICAL, OK, WARNING, check_installation_vs_reading

from .models import InstallationType, SourceSystem

# ANLAGE_KURZ in the prototype
INSTALLATION_SHORT = {
    InstallationType.RADIO: "Funk",
    InstallationType.RADIO_GATEWAY: "Funk+GW",
    InstallationType.PARTIAL_RADIO: "Teilfunk",
    InstallationType.SONTEX_MANUAL: "566+man.",
    InstallationType.MANUAL: "manuell",
}

# CSS class for the coloured left border of the "Anlage" column
INSTALLATION_CSS = {
    InstallationType.RADIO: "anl-radio",
    InstallationType.RADIO_GATEWAY: "anl-radio",
    InstallationType.PARTIAL_RADIO: "anl-partial",
    InstallationType.SONTEX_MANUAL: "anl-sontex",
    InstallationType.MANUAL: "anl-manual",
}


def short_region(region):
    """'Region Böblingen/Sindelfingen/Holzgerlingen' -> 'Böblingen'."""
    return re.split(r"[/(]", (region or "").replace("Region ", ""))[0].strip()


def reading_type_code(reading_type):
    """'MANU - Betreten der Wohnung' -> 'MANU'."""
    return (reading_type or "").split(" - ")[0]


def device_chips(building):
    """Other devices as three groups of chips: heat | water | safety/collectors.

    Port of geraeteCell(). Returns [[(text, css_class), ...], [...], [...]].
    Only groups that contain chips are returned, so the template can draw a
    separator between every two groups. An empty result means "only heat
    cost allocators".
    """
    heat, water, rest = [], [], []
    # CEOS combines heat and warm-water meters in WWZ, so WMZ is not shown there.
    if building.source_system != SourceSystem.CEOS and building.wmz_count:
        heat.append((f"{building.wmz_count} WMZ", "heat"))
    if building.has_wwmz:
        heat.append(("WWMZ", "yes"))
    if building.wwz_count:
        water.append((f"{building.wwz_count} WWZ", "water"))
    if building.kwz_count:
        water.append((f"{building.kwz_count} KWZ", "water"))
    if building.has_gwz:
        water.append(("GWZ", "yes"))
    if building.has_hwz:
        water.append(("HWZ", "yes"))
    if building.has_rwm:
        rest.append(((f"{building.rwm_count} " if building.rwm_count else "") + "RWM", "safety"))
    if building.sz_count:
        rest.append((f"{building.sz_count} SZ", "collector"))
    return [group for group in (heat, water, rest) if group]


@dataclass
class Visit:
    """One reading or installation appointment shown in the list."""

    date: object = None
    time: object = None
    people: list = field(default_factory=list)
    order: object = None


@dataclass
class Schedule:
    """Everything for the "Ablesung und Montage" column of one building."""

    reading: Visit
    installations: list  # Visits with a date, earliest first
    orders_without_date: int
    order_count: int
    check: object = None  # conflicts.rules.DateCheck or None (no order)
    installation_first: bool = False
    same_person: bool = False

    @property
    def css(self):
        if not self.order_count:
            return ""
        if self.check.severity == CRITICAL:
            return "crit"
        if self.check.severity == WARNING:
            return "warn"
        return "ok"

    @property
    def note_css(self):
        """CSS class of the footnote under the column."""
        return {CRITICAL: "crit", WARNING: "warn", OK: "ok"}.get(self.check.severity, "info") if self.check else "info"

    @property
    def first_installation(self):
        return self.installations[0] if self.installations else None


def building_schedule(building):
    """Build the Schedule from the prefetched stops (see selectors.py)."""
    reading_stop = building.reading_stops[0] if building.reading_stops else None
    if reading_stop:
        reading = Visit(date=reading_stop.tour.date, people=[reading_stop.tour.employee.short_name])
    else:
        # Not planned yet: show the responsible reader without a date.
        reading = Visit(people=[building.assigned_reader.short_name] if building.assigned_reader else [])

    installations, without_date = [], 0
    for order in building.list_orders:
        stops = list(order.tour_stops.all())
        if not stops:
            without_date += 1
            continue
        installations.append(Visit(
            date=stops[0].tour.date,
            time=stops[0].start_time or stops[0].tour.start_time,
            people=sorted({stop.tour.employee.short_name for stop in stops}),
            order=order,
        ))
    installations.sort(key=lambda visit: (visit.date, visit.time))

    schedule = Schedule(
        reading=reading,
        installations=installations,
        orders_without_date=without_date,
        order_count=len(building.list_orders),
    )
    if schedule.order_count:
        first = schedule.first_installation
        schedule.check = check_installation_vs_reading(reading.date, first.date if first else None)
        schedule.installation_first = bool(first and (reading.date is None or first.date < reading.date))
        schedule.same_person = bool(first and set(first.people) & set(reading.people))
    return schedule
