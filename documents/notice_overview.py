"""
📄 Aushänge per Fahrplan (for the Terminierung) and the 🗺 Aushang-Route (a printed list for the
person who hangs them). The rules are in notice_rules.py (pure), the papers in notices.py.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from planning.models import StopKind, Tour, TourStop

from . import notice_rules as rules
from . import notices

PRINTED, OUTDATED = rules.PRINTED, rules.OUTDATED


@dataclass
class NoticeStop:
    stop: object
    state: str              # "" (kein Aushang) / missing / late / printed / outdated
    window: tuple | None
    source: str             # manual / plan / estimate
    flats: list             # [] = one Aushang for the house, else one Brief per flat

    @property
    def time(self):
        return notices.time_label(self.window)

    @property
    def ref(self):
        s = self.stop
        return f"AZ {s.building.file_number}" if s.kind == StopKind.READING else f"RE {s.installation_order.re_number}"

    @property
    def address(self):
        target = self.stop.building or self.stop.installation_order
        return f"{target.street}, {target.zip_code} {target.city}"

    @property
    def open(self):
        return self.state in (rules.MISSING, rules.LATE, OUTDATED)

    @property
    def undecided(self):
        """The Terminierung has not chosen yet (Aushang / Briefe / telefonisch / per Mail / keine)."""
        return not self.stop.notice_choice and not self.stop.done_at


@dataclass
class Block:
    tour: object
    stops: list
    deadline: datetime.date

    @property
    def wanted(self):
        return [s for s in self.stops if s.state]

    @property
    def printed(self):
        return [s for s in self.stops if s.state == PRINTED]

    @property
    def missing(self):
        return [s for s in self.stops if s.open]

    @property
    def late(self):
        return any(s.state == rules.LATE for s in self.stops)

    @property
    def undecided(self):
        return [s for s in self.stops if s.undecided]

    @property
    def wanted_stops(self):
        return [s.stop for s in self.wanted]

    @property
    def missing_stops(self):
        return [s.stop for s in self.missing]

    @property
    def papers(self):
        return sum(len(s.flats) or 1 for s in self.wanted)


def blocks(today, horizon=28, only_open=False, query="", tour=None, ready=False, undecided=False):
    """One block per coming Fahrplan with every appointment and its Aushang: wanted?, printed?"""
    tours = (Tour.objects.filter(date__gte=today).select_related("employee").prefetch_related("team")
             .order_by("date", "employee__short_name"))
    if tour is not None:
        tours = tours.filter(pk=tour.pk)
    if horizon:
        tours = tours.filter(date__lte=today + datetime.timedelta(days=horizon))
    stops = {}
    for stop in (TourStop.objects.filter(tour__in=tours, kind__in=[StopKind.READING, StopKind.INSTALLATION])
                 .select_related("tour", "building", "installation_order", "notice_printed_by").order_by("position")):
        stops.setdefault(stop.tour_id, []).append(stop)
    words = query.lower().split()
    found = []
    for tour in tours:
        mine = stops.get(tour.pk, [])
        if not mine:
            continue
        estimates = notices.estimated_times(tour)
        items = []
        for stop in mine:
            window, source = notices.window_of(stop, estimates)
            state = notices.state_of(stop, today, estimates).state if notices.wanted(stop) else ""
            flats = rules.papers(stop.notice_scope, stop.notice_units)
            items.append(NoticeStop(stop, state, window, source, [f for f in flats if f]))
        if words:
            text = " ".join([tour.people_label] + [f"{i.ref} {i.address}" for i in items]).lower()
            if not all(w in text for w in words):
                continue
        block = Block(tour, items, rules.notice_deadline(tour.date))
        if only_open and not block.missing:
            continue
        if ready and not block.printed:     # 🚗 printed = ready to be handed out (no fixed day for that)
            continue
        if undecided and not block.undecided:   # ❓ the Terminierung still has to choose
            continue
        found.append(block)
    return found


def block_of(tour, today):
    found = blocks(today, None, tour=tour) if tour.date >= today else []
    return found[0] if found else None


def notice_stop(stop, today, estimates=None):
    estimates = notices.estimated_times(stop.tour) if estimates is None else estimates
    window, source = notices.window_of(stop, estimates)
    state = notices.state_of(stop, today, estimates).state if notices.wanted(stop) else ""
    flats = [f for f in rules.papers(stop.notice_scope, stop.notice_units) if f]
    return NoticeStop(stop, state, window, source, flats)


# --- 🗺 Aushang-Route ---------------------------------------------------------------------------------

@dataclass
class RouteStop:
    n: int
    target: object                   # building / order with the address
    point: tuple | None
    point_source: str | None
    papers: list = field(default_factory=list)   # [(ref, "Aushang" or "Brief Whg 3", appointment date, what)]
    drive_minutes: int = 0           # to this house
    drive_km: Decimal | None = None
    drive_from_tomtom: bool = False
    arrive: int = 0
    leave: int = 0
    line: list = field(default_factory=list)     # road points of the drive to this house (TomTom)
    stop_ids: list = field(default_factory=list)  # the appointments whose papers go to this house
    far_km: float | None = None      # ⚠ the nearest other house is further away than FAR_KM

    @property
    def address(self):
        return f"{self.target.street}, {self.target.zip_code} {self.target.city}"

    @property
    def aushaenge(self):
        return sum(1 for p in self.papers if p[1] == "Aushang")

    @property
    def briefe(self):
        return sum(1 for p in self.papers if p[1] != "Aushang")

    @property
    def minutes(self):
        return rules.stop_minutes(self.aushaenge, self.briefe)

    @property
    def arrive_text(self):
        return f"{self.arrive // 60:02d}:{self.arrive % 60:02d}"

    @property
    def after_text(self):
        """Without a start time: how long after leaving the office ('35 min', '1:20 h')."""
        return rules.duration_text(self.arrive)


def office(client=None):
    """(address, (lat, lon), source) of the office - every Aushang-Route starts and ends there."""
    from django.conf import settings
    from django.core.cache import cache

    from planning.geocoding import zip_centre
    from planning.tomtom import TomTomError

    address = f"{settings.OFFICE_STREET}, {settings.OFFICE_ZIP} {settings.OFFICE_CITY}"
    if client is not None:
        import hashlib
        key = "office-point-" + hashlib.sha1(address.encode()).hexdigest()
        point = cache.get(key)
        if point is None:
            try:
                found = client.geocode(address)
                point = (found.latitude, found.longitude)
                cache.set(key, point, 60 * 60 * 24 * 30)
            except TomTomError:
                point = None
        if point is not None:
            return address, tuple(point), "tomtom"
    return address, zip_centre(settings.OFFICE_ZIP), "zip"


@dataclass
class Leg:
    minutes: int = 0
    km: Decimal | None = None
    from_tomtom: bool = False
    line: list = field(default_factory=list)
    arrive: int = 0
    order_source: str = ""           # "tomtom" (best order on real roads) / "luftlinie"


MAX_BEST_ORDER = 50                  # more houses: our own order (TomTom's best order is for normal routes)


def _leg(client, a, a_tomtom, b, b_tomtom, departure):
    """Drive a -> b: TomTom when both positions come from TomTom, else estimated."""
    from planning.rules.drive_time import distance_km, estimate_drive_minutes, planned_drive_minutes
    from planning.tomtom import TomTomError

    if a is None or b is None:
        return Leg(15)
    leg = Leg(estimate_drive_minutes(a, b), Decimal(round(distance_km(a, b), 1)).quantize(Decimal("0.1")))
    if client is not None and a_tomtom and b_tomtom:
        try:
            found = client.route(a, b, departure)
            return Leg(planned_drive_minutes(found.seconds, found.meters),
                       Decimal(round(found.meters / 1000, 1)).quantize(Decimal("0.1")), True, found.points)
        except TomTomError:
            pass
    return leg


def route(stop_ids, date=None, start_time=None, client=None):
    """The chosen appointments as one round trip office -> houses -> office: one stop per house
    (several papers at one house together). Without a start time the times are counted from 0:00.
    Returns (houses, the drive back to the office, the office)."""
    from planning import geocoding
    from planning.tomtom import local_datetime

    chosen = (TourStop.objects.filter(pk__in=stop_ids, kind__in=[StopKind.READING, StopKind.INSTALLATION])
              .select_related("tour", "building", "installation_order__building").order_by("tour__date", "position"))
    houses = {}
    for stop in chosen:
        target = stop.building or stop.installation_order.building or stop.installation_order
        key = (type(target).__name__, target.pk)
        house = houses.setdefault(key, {"target": target, "papers": [], "stops": []})
        house["stops"].append(stop.pk)
        ref = f"AZ {stop.building.file_number}" if stop.kind == StopKind.READING else f"RE {stop.installation_order.re_number}"
        what = "Ablesung" if stop.kind == StopKind.READING else "Montage"
        for flat in rules.papers(stop.notice_scope, stop.notice_units):
            house["papers"].append((ref, f"Brief {flat}" if flat else "Aushang", stop.tour.date, what))
    found = []
    for house in houses.values():
        point, source, _ = geocoding.position(house["target"], client)
        found.append(RouteStop(0, house["target"], point, source, house["papers"], stop_ids=house["stops"]))
    base = office(client)
    order, order_source = _best_order(found, base, client)
    found = [found[i] for i in order]
    for i, km in rules.far_away([s.point for s in found]).items():
        found[i].far_km = km
    # TomTom needs a departure: the chosen day/time, else the next working day at 8:00 (only for the traffic)
    day = date or (datetime.date.today() + datetime.timedelta(days=1))
    clock = start_time or datetime.time(8, 0)
    t = clock.hour * 60 + clock.minute if start_time else 0       # without a start time: counted from 0:00
    offset = 0 if start_time else clock.hour * 60 + clock.minute  # ... but TomTom gets a real clock time

    def departure(minutes):
        minutes = min(minutes + offset, 23 * 60 + 59)
        return local_datetime(day, datetime.time(minutes // 60, minutes % 60))

    here, here_tomtom = base[1], base[2] == "tomtom"
    for n, stop in enumerate(found, start=1):
        stop.n = n
        leg = _leg(client, here, here_tomtom, stop.point, stop.point_source == "tomtom", departure(t))
        stop.drive_minutes, stop.drive_km, stop.drive_from_tomtom, stop.line = leg.minutes, leg.km, leg.from_tomtom, leg.line
        t += stop.drive_minutes
        stop.arrive, stop.leave = t, t + stop.minutes
        t = stop.leave
        here, here_tomtom = stop.point or here, stop.point_source == "tomtom"
    back = _leg(client, here, here_tomtom, base[1], base[2] == "tomtom", departure(t)) if found else Leg()
    back.arrive = t + back.minutes
    back.order_source = order_source
    return found, back, base


def _best_order(found, base, client):
    """The order of the houses: TomTom's best order on the real roads office -> houses -> office when the
    key is set (and every house has a TomTom position); else nearest house next + no detours (straight line)."""
    from planning.tomtom import TomTomError

    points = [s.point for s in found]
    if (client is not None and base[1] is not None and base[2] == "tomtom" and 2 <= len(found) <= MAX_BEST_ORDER
            and all(s.point is not None and s.point_source == "tomtom" for s in found)):
        try:
            order = client.best_order([base[1], *points, base[1]])
            if sorted(order) == list(range(len(found))):
                return order, "tomtom"
        except (TomTomError, KeyError, TypeError):
            pass
    return rules.route_order(points, base[1], end=base[1]), "luftlinie"


def route_areas(found):
    """The houses of a route by area: [{"name", "houses", "stop_ids"}] - more than one = better split."""
    groups = rules.areas([s.point for s in found])
    return [{"name": rules.area_name([found[i].target.city for i in g]), "houses": len(g),
             "stop_ids": [pk for i in g for pk in found[i].stop_ids]} for g in groups]


def page_areas(blocks_found):
    """Areas of the houses with an Aushang on the page (for "☑ alle in diesem Gebiet")."""
    from planning import geocoding

    houses = {}
    for block in blocks_found:
        for item in block.wanted:
            target = item.stop.building or item.stop.installation_order
            house = houses.setdefault((type(target).__name__, target.pk), {"target": target, "stops": []})
            house["stops"].append(item.stop.pk)
    houses = list(houses.values())
    points = [geocoding.position(h["target"], None)[0] for h in houses]   # stored positions only - no TomTom calls
    return [{"name": rules.area_name([houses[i]["target"].city for i in g]), "houses": len(g),
             "stop_ids": [pk for i in g for pk in houses[i]["stops"]]} for g in rules.areas(points)]


def route_map_data(found, back, base):
    """Pins for static/js/route_map.js: the office (B, green), the houses (blue), the line there and back."""
    from planning.display import START, STOP

    pins = []
    if base[1] is not None:
        pins.append({"n": "B", "lat": base[1][0], "lon": base[1][1], "label": f"Büro – {base[0]}", "time": "",
                     "colour": START, "done": False})
    for s in found:
        if s.point is not None:
            pins.append({"n": s.n, "lat": s.point[0], "lon": s.point[1], "label": s.address, "time": s.arrive_text,
                         "colour": STOP, "done": False})
    legs = [s.line for s in found] + [back.line]
    if all(legs):
        lines = legs
    else:
        points = [[p["lon"], p["lat"]] for p in pins]
        lines = [points + points[:1]] if len(points) > 1 else []
    return {"pins": pins, "lines": lines}
