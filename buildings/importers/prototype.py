"""
Import of the demo data embedded in the two HTML prototypes.

Deckblätter dashboard:
    const DATA             240 buildings (JSON)
    const META             global Stichtag "31.12.2026" (JSON)
    const EMBEDDED_MONTAGE 70 installation orders with date/installer (JSON)
    const ABLESER          reader names (JS list)
Montage dashboard:
    const RAW              the same 70 orders with contract lines (JS objects)
    const MONTEURE         installer names (JS list)

Part 1 of this file only *reads* the HTML (pure functions, easy to test).
Part 2 writes everything to the database in one transaction.
"""

import datetime
import json
import re
import unicodedata
from dataclasses import dataclass, field

from django.contrib.auth.models import Group, User
from django.db import transaction
from django.utils import timezone

from buildings.models import (
    Building,
    BuildingStatus,
    DeviceCategory,
    InstallationOrder,
    InstallationOrderItem,
    OrderPriority,
    OrderStatus,
    PropertyManager,
    SourceSystem,
)
from buildings.rules.file_numbers import extract_re_numbers, normalize_file_number
from buildings.services import apply_access
from buildings.rules.installation_time import DEFAULT_CATEGORIES, classify_article, installation_minutes
from buildings.rules.material import DEFAULT_PRICES
from buildings.rules.installation_type import guess_installation_type
from buildings.rules.reading_time import planned_reading_minutes
from core import roles
from documents.models import CostDocumentReceipt, CoverSheet
from planning.models import Employee, RoutingSource, StopKind, Tour, TourStatus, TourStop

# =============================================================================
# Part 1: reading the HTML
# =============================================================================


class PrototypeFormatError(Exception):
    """The HTML file does not contain what we expect."""


def js_literal_to_json(text):
    """Turn a JavaScript object/array literal into valid JSON text.

    Handles the two differences that occur in the prototypes:
    unquoted keys ({re:"RE1"} -> {"re":"RE1"}), single-quoted strings
    ('Kaiser' -> "Kaiser") and trailing commas ([1,2,] -> [1,2]).
    Text inside strings is copied unchanged.
    """
    out = []
    i = 0
    while i < len(text):
        char = text[i]
        if char in "\"'":  # copy a whole string literal
            end = i + 1
            while text[end] != char:
                end += 2 if text[end] == "\\" else 1
            content = text[i + 1:end]
            out.append(json.dumps(content.replace("\\'", "'")) if char == "'" else text[i:end + 1])
            i = end + 1
        elif char.isalpha() or char == "_":  # bare word: key, true/false/null
            end = i
            while end < len(text) and (text[end].isalnum() or text[end] == "_"):
                end += 1
            word = text[i:end]
            rest = text[end:].lstrip()
            out.append(f'"{word}"' if rest.startswith(":") else word)
            i = end
        elif char == ",":  # drop trailing commas before ] or }
            rest = text[i + 1:].lstrip()
            if not rest.startswith(("]", "}")):
                out.append(char)
            i += 1
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _find_literal(html, name):
    """Return the text of the literal after 'const NAME =' (brackets balanced)."""
    match = re.search(r"const\s+" + re.escape(name) + r"\s*=\s*", html)
    if not match:
        raise PrototypeFormatError(f"'const {name}' not found in the HTML file.")
    start = match.end()
    opening = html[start]
    if opening not in "[{":
        raise PrototypeFormatError(f"'const {name}' is not a list or object.")
    closing = "]" if opening == "[" else "}"
    depth, i, quote = 0, start, None
    while i < len(html):
        char = html[i]
        if quote:
            if char == "\\":
                i += 1
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
        i += 1
    raise PrototypeFormatError(f"'const {name}' is not closed.")


def extract_constant(html, name):
    """Read 'const NAME = ...' from the HTML and return it as Python data."""
    literal = _find_literal(html, name)
    try:
        return json.loads(literal)
    except json.JSONDecodeError:
        return json.loads(js_literal_to_json(literal))


def parse_german_date(text):
    """'31.12.2026' -> date(2026, 12, 31)."""
    day, month, year = (int(part) for part in text.strip().split("."))
    return datetime.date(year, month, day)


def parse_iso_date(text):
    return datetime.date.fromisoformat(text[:10]) if text else None


def yes_no(value):
    """'Ja' -> True, 'Nein' -> False, anything else -> None (unknown)."""
    return {"Ja": True, "Nein": False}.get(value)


def blank_dash(value):
    """The prototype uses '-' for 'no value'."""
    value = (value or "").strip() if isinstance(value, str) else value
    return "" if value in ("-", None) else value


def calendar_color(name):
    """Colour per person, same as calFarbe() in the prototype calendar."""
    palette = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#42d4f4",
               "#f032e6", "#bfef45", "#fabed4", "#469990", "#dcbeff", "#9A6324", "#808000", "#000075"]
    h = 0
    for char in name:
        h = (h * 31 + ord(char)) & 0xFFFFFFFF
    return palette[h % len(palette)]


def username_for(name):
    """'Schäfer' -> 'schaefer' (ASCII login name)."""
    name = name.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()


SOURCE_MAP = {"BFW": SourceSystem.BFW_MAIN, "CEOS": SourceSystem.CEOS}
BUILDING_STATUS_MAP = {"offen": BuildingStatus.OPEN, "nacharbeit": BuildingStatus.REWORK, "freigegeben": BuildingStatus.RELEASED}
ORDER_STATUS_MAP = {label: value for value, label in OrderStatus.choices}  # "Verplant" -> "planned"
ORDER_PRIORITY_MAP = {label: value for value, label in OrderPriority.choices}


@dataclass
class PrototypeData:
    """Everything read from the two HTML files."""

    stichtag: datetime.date
    buildings: list
    orders: list
    order_items: dict = field(default_factory=dict)  # re_number -> {"client":..., "items": [...]}
    readers: list = field(default_factory=list)
    installers: list = field(default_factory=list)


def read_prototypes(deckblatt_html, montage_html=None):
    meta = extract_constant(deckblatt_html, "META")
    data = PrototypeData(
        stichtag=parse_german_date(meta["stichtag"]),
        buildings=extract_constant(deckblatt_html, "DATA"),
        orders=extract_constant(deckblatt_html, "EMBEDDED_MONTAGE")["orders"],
        readers=extract_constant(deckblatt_html, "ABLESER"),
    )
    if montage_html:
        data.installers = extract_constant(montage_html, "MONTEURE")
        for raw in extract_constant(montage_html, "RAW"):
            data.order_items[raw["re"]] = {"client": raw.get("auftraggeber", ""), "items": raw.get("items", [])}
    return data


# =============================================================================
# Part 2: writing to the database
# =============================================================================

IMPORT_REASON = "Import aus HTML-Prototyp"


@dataclass
class ImportResult:
    counts: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def add(self, key, number=1):
        self.counts[key] = self.counts.get(key, 0) + number


def _save(instance):
    """Save with a reason that appears in the change history."""
    instance._change_reason = IMPORT_REASON
    instance.save()


@transaction.atomic
def import_prototype_data(data):
    """Create or update all records. Running it twice does not duplicate data."""
    result = ImportResult()
    employees = _import_employees(data, result)
    categories = _import_categories(result)
    buildings = _import_buildings(data, employees, result)
    orders = _import_orders(data, buildings, employees, categories, result)
    _import_planned_tours(data, buildings, orders, employees, result)
    return result


def _import_employees(data, result):
    reader_group = Group.objects.get(name=roles.READER)
    employees = {}
    for name in dict.fromkeys(data.readers + data.installers):  # unique, keeps order
        user, created = User.objects.get_or_create(username=username_for(name), defaults={"last_name": name})
        if created:
            user.set_unusable_password()  # an admin sets a password later
            user.save()
        user.groups.add(reader_group)
        employee, _ = Employee.objects.update_or_create(
            user=user,
            defaults={
                "short_name": name,
                "can_read": name in data.readers,
                "can_install": name in data.installers,
                "can_notice": name in data.readers[:2],  # demo: two people also hang the Aushänge
                "calendar_color": calendar_color(name),
            },
        )
        employees[name] = employee
        result.add("Mitarbeiter")
    return employees


def _import_categories(result):
    categories = {}
    for code, (label, minutes) in DEFAULT_CATEGORIES.items():
        # get_or_create: minutes and prices changed later in the admin are kept
        categories[code], _ = DeviceCategory.objects.get_or_create(
            code=code, defaults={"label": label, "minutes_per_piece": minutes, "price": DEFAULT_PRICES.get(code, 0)}
        )
    result.add("Gerätekategorien", len(categories))
    return categories


def _import_buildings(data, employees, result):
    stichtag = data.stichtag
    buildings = {}  # prototype "nr" -> Building
    for row in data.buildings:
        source = SOURCE_MAP.get(row["quelle"])
        if source is None:
            result.warnings.append(f"Liegenschaft {row['nr']}: unbekannte Quelle '{row['quelle']}' übersprungen")
            continue

        manager_name = (row.get("hv_deckblatt") or "").strip()
        manager = PropertyManager.objects.get_or_create(name=manager_name)[0] if manager_name else None

        reader = employees.get(row.get("ableser") or "")
        counts = {
            "hkv_count": row.get("hkv") or 0,
            "wmz_count": row.get("wmz") or 0,
            "wwz_count": row.get("wwz") or 0,
            "kwz_count": row.get("kwz") or 0,
            "rwm_count": row.get("rwm_n") or 0,
        }
        building = Building.objects.filter(source_system=source, file_number=row["nr"]).first() or Building(
            source_system=source, file_number=row["nr"]
        )
        values = {
            "street": row["strasse"],
            "zip_code": row["plz"],
            "city": row["ort"],
            "stichtag": stichtag,
            "billing_period_start": datetime.date(stichtag.year, 1, 1) if (stichtag.month, stichtag.day) == (12, 31) else None,
            "billing_period_end": stichtag,
            "region": row.get("region") or "",
            "status": BUILDING_STATUS_MAP.get(row.get("status"), BuildingStatus.OPEN),
            "assigned_reader": reader,
            "property_manager": manager,
            "reading_type": blank_dash(row.get("ableseart")),
            "installation_type": guess_installation_type(row.get("ableseart"), row.get("hkv_familie"), bool(row.get("gateway"))),
            "hkv_type": blank_dash(row.get("hkv_typ")),
            "hkv_family": row.get("hkv_familie") or "",
            "hkv_variant": blank_dash(row.get("hkv_variante")),
            "has_gateway": bool(row.get("gateway")),
            "apartments": row.get("wohnungen") or 0,
            **counts,
            "sz_count": row.get("sz") or 0,
            "hwmz_count": row.get("hwmz") or 0,
            "has_rwm": yes_no(row.get("rwm")),
            "has_gwz": yes_no(row.get("gwz")),
            "has_hwz": yes_no(row.get("hwz")),
            "has_wwmz": yes_no(row.get("wwmz")),
            "detail_heat": blank_dash(row.get("detail_warm")),
            "detail_cold_water": blank_dash(row.get("detail_kwz")),
            "remark": row.get("bemerkung") or "",
            "note": row.get("notiz") or "",
            "handwritten_note": row.get("handschrift") or "",
            "order_reference": blank_dash(row.get("auftrag")),
            "reading_minutes_calculated": planned_reading_minutes(
                hkv_family=row.get("hkv_familie"), apartments=row.get("wohnungen") or 0,
                reading_type=row.get("ableseart"), **counts,
            ),
            "raw_data": row,
        }
        for name, value in values.items():
            setattr(building, name, value)
        apply_access(building)
        _save(building)
        buildings[row["nr"]] = building
        result.add("Liegenschaften")

        CoverSheet.objects.update_or_create(
            building=building,
            defaults={
                "source_file": row.get("quelldatei") or "",
                "page": row.get("seite"),
                "source_label": row.get("hv_quelle") or "",
                "owner_text": row.get("hv_eigentuemer") or "",
                "property_manager_text": manager_name,
            },
        )
        result.add("Deckblätter")

        received = parse_iso_date(row.get("unterlagen_datum"))
        if received and not CostDocumentReceipt.objects.filter(building=building).exists():
            receipt = CostDocumentReceipt(
                building=building,
                received_on=received,
                deadline_start=received,
                last_seen_status=building.status,
                last_seen_planned_date=parse_iso_date(row.get("geplant_datum")),
            )
            _save(receipt)
            result.add("Unterlagen-Eingänge")
    return buildings


def _find_building_for_order(order_row, buildings_by_re, buildings_by_core):
    """Same order as in the prototype: first via RE number, then building number."""
    building = buildings_by_re.get(order_row["re"].upper())
    if building:
        return building
    matches = buildings_by_core.get(normalize_file_number(order_row.get("lieg")), [])
    return matches[0] if matches else None


def _import_orders(data, buildings, employees, categories, result):
    buildings_by_re, buildings_by_core = {}, {}
    for building in buildings.values():
        for re_number in extract_re_numbers(building.order_reference, building.handwritten_note):
            buildings_by_re[re_number] = building
        buildings_by_core.setdefault(building.file_number_core, []).append(building)
    minutes_per_category = {code: cat.minutes_per_piece for code, cat in categories.items()}

    orders = {}
    for row in data.orders:
        extra = data.order_items.get(row["re"], {"client": "", "items": []})
        building = _find_building_for_order(row, buildings_by_re, buildings_by_core)
        items = [(article, description, quantity, classify_article(article, description))
                 for article, description, quantity in extra["items"]]
        calculated = installation_minutes([(cat, qty) for _, _, qty, cat in items], minutes_per_category)
        prototype_minutes = row.get("montagezeit")

        order = InstallationOrder.objects.filter(re_number=row["re"]).first() or InstallationOrder(re_number=row["re"])
        values = {
            "process_number": row.get("vorgang") or "",
            "building": building,
            "building_file_number": row.get("lieg") or "",
            "street": row.get("adresse") or "",
            "zip_code": row.get("plz") or "",
            "city": row.get("ort") or "",
            "client": extra["client"],
            "order_date": parse_iso_date(row.get("auftragsdatum")),
            "status": ORDER_STATUS_MAP.get(row.get("status"), OrderStatus.OPEN),
            "priority": ORDER_PRIORITY_MAP.get(row.get("prio"), ""),
            "duration_minutes_calculated": calculated,
            # In the prototype the time from the Fahrplan overrides the calculation.
            "duration_minutes_manual": prototype_minutes if prototype_minutes not in (None, calculated) else None,
            "summary": row.get("kurz") or "",
            "raw_data": {**row, "items": extra["items"]},
        }
        for name, value in values.items():
            setattr(order, name, value)
        _save(order)
        order.assigned_installers.set([employees[name] for name in row.get("monteure", []) if name in employees])

        order.items.all().delete()
        InstallationOrderItem.objects.bulk_create([
            InstallationOrderItem(order=order, article_number=article, description=description,
                                  quantity=quantity, category=categories.get(cat))
            for article, description, quantity, cat in items
        ])
        orders[row["re"]] = order
        result.add("Montageaufträge")
        result.add("Montageaufträge mit Liegenschaft" if building else "Montageaufträge ohne Liegenschaft")
    return orders


def _get_tour(employee, date, start_time, result):
    tour, created = Tour.objects.get_or_create(
        employee=employee, date=date,
        defaults={"start_time": start_time, "status": TourStatus.PROVISIONAL, "routing_source": RoutingSource.NONE,
                  "routing_note": IMPORT_REASON},
    )
    if created:
        result.add("vorläufige Fahrpläne")
    return tour


def _add_stop(tour, **fields):
    """Append a stop unless the same building/order is already in this tour."""
    lookup = {key: fields[key] for key in ("kind", "building", "installation_order") if fields.get(key)}
    if tour.stops.filter(**lookup).exists():
        return False
    stop = TourStop(tour=tour, position=tour.stops.count() + 1, **fields)
    _save(stop)
    return True


def _import_planned_tours(data, buildings, orders, employees, result):
    """Planned days from the prototype become PROVISIONAL tours without drive times.

    They cannot be confirmed until they have been recalculated with TomTom.
    """
    for row in sorted(data.buildings, key=lambda r: r["id"]):
        employee = employees.get(row.get("ableser") or "")
        date = parse_iso_date(row.get("geplant_datum"))
        if not (employee and date and row["nr"] in buildings):
            continue
        building = buildings[row["nr"]]
        tour = _get_tour(employee, date, employee.default_start_time, result)
        if _add_stop(tour, kind=StopKind.READING, building=building, work_minutes=building.reading_minutes):
            result.add("Stopps Ablesung")

    for row in data.orders:
        if not row.get("termin"):
            continue
        order = orders[row["re"]]
        start = datetime.datetime.fromisoformat(row["termin"])
        for name in row.get("monteure", []):
            employee = employees.get(name)
            if not employee:
                continue
            tour = _get_tour(employee, start.date(), start.time(), result)
            done_at = None
            if order.status == OrderStatus.DONE:
                done_at = timezone.make_aware(start)
            if _add_stop(tour, kind=StopKind.INSTALLATION, installation_order=order, building=order.building,
                         work_minutes=order.duration_minutes, done_at=done_at):
                result.add("Stopps Montage")

    for tour in Tour.objects.filter(routing_note=IMPORT_REASON):
        tour.work_minutes = sum(stop.work_minutes for stop in tour.stops.all())
        _save(tour)
