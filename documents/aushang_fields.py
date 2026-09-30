"""
What goes into which field of the company notice template ("VorlageAushänge").
Pure functions: plain data in, field values out - no database.

The template (documents/vorlage/Aushang_Vorlage.dotx) has Word form fields at
fixed places; their names come from the template:

  Text12            top left: Liegenschaftsnummer (AZ)
  Text2             "Wir haben den Auftrag bei Ihnen in der Liegenschaft ____": the address
  Kontrollkästchen1-4  Ablesung / Montage / Austausch / Wartung-Sichtkontrolle
  Kontrollkästchen5-8  Heizkostenverteiler / Wasserzähler / Wärmezähler / Rauchwarnmelder
  Dropdown1         "am:" weekday Montag..Samstag
  Text5             ", dem:" date
  Text4             "zwischen / ab:" time, e.g. "09:00 – 11:00 Uhr"
  Text10            bottom, free text (empty by default)
"""

import datetime
from dataclasses import dataclass, field

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"]  # the dropdown of the template
ALL_WEEKDAYS = WEEKDAYS + ["Sonntag"]  # Sonntag is added to the dropdown when needed

WORK_BOXES = ["ablesung", "montage", "austausch", "wartung"]
DEVICE_BOXES = ["hkv", "wasser", "waerme", "rwm"]
BOX_FIELDS = {  # our name -> form field name in the template
    "ablesung": "Kontrollkästchen1", "montage": "Kontrollkästchen2",
    "austausch": "Kontrollkästchen3", "wartung": "Kontrollkästchen4",
    "hkv": "Kontrollkästchen5", "wasser": "Kontrollkästchen6",
    "waerme": "Kontrollkästchen7", "rwm": "Kontrollkästchen8",
}
BOX_LABELS = {
    "ablesung": "Ablesung", "montage": "Montage", "austausch": "Austausch", "wartung": "Wartung/Sichtkontrolle",
    "hkv": "Heizkostenverteiler", "wasser": "Wasserzähler", "waerme": "Wärmezähler", "rwm": "Rauchwarnmelder",
}

# device category codes of installation orders -> device box
CATEGORY_BOXES = {"EHKV": "hkv", "SQ1": "wasser", "WMZ": "waerme", "RWM": "rwm"}


@dataclass(frozen=True)
class Devices:
    """What is in the building / in the order."""

    hkv: bool = False
    water: bool = False
    heat: bool = False
    rwm: bool = False


@dataclass
class NoticeFields:
    number: str = ""                 # Text12
    address: str = ""                # Text2
    weekday: int | None = None       # Dropdown1: 0 = Montag .. 6 = Sonntag (added to the list), None = unknown
    date: str = ""                   # Text5 "03.11.2026"
    time: str = ""                   # Text4 "09:00 – 11:00 Uhr"
    bottom: str = ""                 # Text10
    boxes: set = field(default_factory=set)   # names from WORK_BOXES / DEVICE_BOXES that are ticked

    @property
    def weekday_name(self):
        return ALL_WEEKDAYS[self.weekday] if self.weekday is not None else ""


def devices_from_counts(hkv=0, wmz=0, wwz=0, kwz=0, rwm=0, hwmz=0, has_rwm=None):
    """Devices of a building from its device counts."""
    return Devices(hkv=hkv > 0, water=(wwz + kwz) > 0, heat=(wmz + hwmz) > 0, rwm=rwm > 0 or bool(has_rwm))


def devices_from_categories(codes):
    """Devices of an installation order from the category codes of its items (EHKV, SQ1, WMZ, RWM ...)."""
    boxes = {CATEGORY_BOXES[c] for c in codes if c in CATEGORY_BOXES}
    return Devices(hkv="hkv" in boxes, water="wasser" in boxes, heat="waerme" in boxes, rwm="rwm" in boxes)


def time_text(window):
    """(09:00, 11:00) -> '09:00 – 11:00 Uhr'; only a start -> 'ab 09:00 Uhr'."""
    if not window:
        return ""
    start, end = window
    return f"{start:%H:%M} – {end:%H:%M} Uhr" if end else f"ab {start:%H:%M} Uhr"


def notice_fields(kind, date, window, number="", address="", devices=Devices(), exchange=False, rwm_check=False):
    """
    kind: "reading" or "installation"; exchange: the order is a Tausch (not a new Montage);
    rwm_check: at this reading the smoke detectors are checked too (Sichtkontrolle).
    """
    boxes = set()
    if kind == "reading":
        boxes.add("ablesung")
        # a reading reads heat cost allocators and meters; smoke detectors only if they are checked
        boxes |= {name for name, has in (("hkv", devices.hkv), ("wasser", devices.water), ("waerme", devices.heat)) if has}
        if rwm_check:
            boxes |= {"wartung", "rwm"}
    else:
        boxes.add("austausch" if exchange else "montage")
        boxes |= {name for name, has in (("hkv", devices.hkv), ("wasser", devices.water),
                                         ("waerme", devices.heat), ("rwm", devices.rwm)) if has}
    weekday = date.weekday() if isinstance(date, datetime.date) else None
    return NoticeFields(
        number=number, address=address, weekday=weekday,
        date=f"{date:%d.%m.%Y}" if date else "", time=time_text(window), boxes=boxes,
    )
