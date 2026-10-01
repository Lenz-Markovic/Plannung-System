"""
🗓 Wochenplanung - planning a whole week BY HAND (pure functions, no database).

The office puts objects on a person and a day; nothing is spread automatically (the frozen
"🤖 Automatisch planen" stays off). These rules only work out the week, the state of a cell,
the hours of a day and hints - and move items between days without duplicates.

An item is {"kind": "reading", "building": id}, {"kind": "installation", "order": id} or a 📄 Aushang-Fahrt
{"kind": "notice", "building": id, "order": None} / {"kind": "notice", "building": None, "order": id}
(the same format as the planning drafts, so a day can be saved like any other plan).
"""

import datetime

READING, INSTALLATION, NOTICE = "reading", "installation", "notice"   # notice = 📄 Aushang-Fahrt
FREE, DRAFT, PLANNED, TEAM, ABSENT = "free", "draft", "planned", "team", "absent"
UNDER_MINUTES = 360      # a day under 6 h is shown as "noch Platz"
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def monday_of(day):
    return day - datetime.timedelta(days=day.weekday())


def default_monday(today):
    """This week from Monday to Thursday, else the next one (Friday: plan the coming week)."""
    return monday_of(today) if today.weekday() < 4 else monday_of(today) + datetime.timedelta(days=7)


def chosen_monday(value, today):
    try:
        return monday_of(datetime.date.fromisoformat(value))
    except (TypeError, ValueError):
        return default_monday(today)


def week_days(monday):
    return [monday + datetime.timedelta(days=i) for i in range(5)]


def week_label(monday):
    friday = monday + datetime.timedelta(days=4)
    return f"KW {monday.isocalendar()[1]} · {monday:%d.%m.} – {friday:%d.%m.%Y}"


def item_key(item):
    if item["kind"] == NOTICE:
        return (NOTICE, item.get("building"), item.get("order"))
    return (item["kind"], item.get("building") if item["kind"] == READING else item.get("order"))


def parse_item(value):
    """'reading:12' / 'installation:7' / 'notice:b12' / 'notice:o7' from the page -> item (None when broken)."""
    if str(value).startswith("notice:"):
        rest = str(value)[len("notice:"):]
        if rest[:1] in ("b", "o") and rest[1:].isdigit():
            pk = int(rest[1:])
            return {"kind": NOTICE, "building": pk if rest[0] == "b" else None, "order": pk if rest[0] == "o" else None}
        return None
    try:
        kind, pk = str(value).split(":")
        pk = int(pk)
    except (ValueError, TypeError):
        return None
    if kind == READING:
        return {"kind": READING, "building": pk}
    if kind == INSTALLATION:
        return {"kind": INSTALLATION, "order": pk}
    return None


def item_value(item):
    if item["kind"] == NOTICE:
        return f"notice:o{item['order']}" if item.get("order") else f"notice:b{item['building']}"
    return f"{item['kind']}:{item_key(item)[1]}"


def can_take(kind, can_read, can_install, can_notice=False):
    if kind == NOTICE:
        return can_notice
    return can_read if kind == READING else can_install


def cell_state(absent, planned, in_team, draft):
    """What a (person, day) cell is: absent and existing plans win - they are never overwritten."""
    if absent:
        return ABSENT
    if planned:
        return PLANNED
    if in_team:
        return TEAM
    return DRAFT if draft else FREE


def day_load(work, drive, max_minutes=450):
    """(total minutes, level): 'over' above the person's maximum, 'under' below 6 h, else 'ok'."""
    total = (work or 0) + (drive or 0)
    if total > max_minutes:
        return total, "over"
    if total < UNDER_MINUTES:
        return total, "under"
    return total, "ok"


def hours(minutes):
    return f"{minutes / 60:.1f}".replace(".", ",") + " h"


def find_day(days, employee, date):
    return next((d for d in days if d["employee"] == employee and d["date"] == date), None)


def place(days, employee, date, items):
    """Put items on (employee, date). An item already somewhere else in the week MOVES here.
    Returns (days, moved_from) - moved_from: the (employee, date) cells an item was taken from."""
    keys = {item_key(i) for i in items}
    moved_from = set()
    kept = []
    for day in days:
        before = len(day["stops"])
        stops = [s for s in day["stops"] if item_key(s) not in keys or (day["employee"] == employee and day["date"] == date)]
        if len(stops) != before:
            moved_from.add((day["employee"], day["date"]))
        if stops:
            kept.append({**day, "stops": stops})
    target = find_day(kept, employee, date)
    if target is None:
        target = {"employee": employee, "date": date, "stops": [], "work": 0, "drive": 0}
        kept.append(target)
    have = {item_key(s) for s in target["stops"]}
    for item in items:
        if item_key(item) not in have:
            target["stops"].append(dict(item))
            have.add(item_key(item))
    kept.sort(key=lambda d: (d["date"], d["employee"]))
    return kept, moved_from


def remove(days, employee, date, item=None):
    """Take one item (or the whole day with item=None) off a cell; an empty day disappears."""
    found = []
    for day in days:
        if day["employee"] == employee and day["date"] == date:
            if item is None:
                continue
            stops = [s for s in day["stops"] if item_key(s) != item_key(item)]
            if not stops:
                continue
            day = {**day, "stops": stops}
        found.append(day)
    return found


def window_hint(date, earliest=None, latest=None):
    """The Montage rule (installation ≥ 8 days before the reading of the same building)."""
    if earliest and date < earliest:
        return f"frühestens am {earliest:%d.%m.} (erst die Montage)"
    if latest and date > latest:
        return f"spätestens am {latest:%d.%m.} (Montage vor der Ablesung)"
    return ""


def pool_sort_key(item):
    """The list "Noch nicht geplant": ✓ ticked first, then 🔁 Nachtermine, then with a deadline,
    then by postcode (so objects close to each other stand together)."""
    deadline = item.get("latest") or item.get("earliest")
    return (not item.get("ticked"), not item.get("revisit"), deadline is None,
            deadline or datetime.date.max, item.get("zip", ""), item.get("street", ""))


def totals(days):
    return {"days": len(days), "stops": sum(len(d["stops"]) for d in days),
            "minutes": sum((d.get("work") or 0) + (d.get("drive") or 0) for d in days)}
