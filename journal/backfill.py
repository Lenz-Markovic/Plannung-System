"""Fill the 🕘 Verlauf once with what happened BEFORE it existed (from django-simple-history).

Used by the migration journal/0002 (with the migration's models) - so after the update the
Verlauf is not empty. Plans, printed notices, status of buildings and orders, with the
person from the history ("System" = import / command).
"""

import datetime

DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
TOUR_STATUS = {"provisional": "vorläufig", "confirmed": "bestätigt", "done": "erledigt"}
BUILDING_STATUS = {"open": "offen", "rework": "Nacharbeit", "released": "freigegeben"}
ORDER_STATUS = {"open": "Offen", "planned": "Verplant", "work_card": "Arbeitskarte", "in_progress": "In Bearbeitung",
                "done": "Erledigt"}
SAME_SAVE = datetime.timedelta(seconds=10)  # one "Speichern" writes several history rows


def _day(date):
    return f"{DAYS[date.weekday()]} {date:%d.%m.%Y}"


def backfill(apps):
    Activity = apps.get_model("journal", "Activity")
    Employee = apps.get_model("planning", "Employee")
    Tour = apps.get_model("planning", "Tour")
    names = dict(Employee.objects.values_list("pk", "short_name"))
    tours = set(Tour.objects.values_list("pk", flat=True))
    entries = []  # (when, Activity)

    last = {}
    for h in apps.get_model("planning", "HistoricalTour").objects.order_by("id", "history_date"):
        before = last.get(h.id)
        last[h.id] = h
        who = names.get(h.employee_id, "?")
        if h.history_type == "+":
            text = f"Fahrplan erstellt ({TOUR_STATUS.get(h.status, h.status)}): {who} {_day(h.date)}"
        elif h.history_type == "-":
            text = f"Fahrplan gelöscht: {who} {_day(h.date)}"
        elif before and (before.employee_id, before.date) != (h.employee_id, h.date):
            text = f"Termin verschoben: {names.get(before.employee_id, '?')} {_day(before.date)} → {who} {_day(h.date)}"
        elif before and before.status != h.status and h.status == "confirmed":
            text = f"Fahrplan bestätigt: {who} {_day(h.date)}"
        elif before and h.history_date - before.history_date < SAME_SAVE:
            continue  # the same save
        else:
            text = f"Fahrplan geändert ({TOUR_STATUS.get(h.status, h.status)}): {who} {_day(h.date)}"
        entries.append((h.history_date, Activity(user_id=h.history_user_id, kind="plan", text=text[:400],
                                                 tour_id=h.id if h.id in tours else None)))

    printed = set()
    for h in (apps.get_model("planning", "HistoricalTourStop").objects.exclude(notice_printed_at=None)
              .order_by("notice_printed_at")):
        key = (h.tour_id, h.notice_printed_at)
        if key in printed:
            continue
        printed.add(key)
        entries.append((h.notice_printed_at, Activity(user_id=h.history_user_id, kind="notice",
                                                      text=f"Aushang gedruckt: {h.notice_for}"[:400],
                                                      tour_id=h.tour_id if h.tour_id in tours else None)))

    for model, kind, labels, target in (("buildings.HistoricalBuilding", "status", BUILDING_STATUS, "building"),
                                        ("buildings.HistoricalInstallationOrder", "order", ORDER_STATUS, "order")):
        app, name = model.split(".")
        previous = {}
        for h in apps.get_model(app, name).objects.order_by("id", "history_date"):
            old = previous.get(h.id)
            previous[h.id] = h.status
            if old is None or old == h.status or h.history_type != "~":
                continue
            label = f"AZ {h.file_number}" if target == "building" else h.re_number
            text = f"{label} {h.street}: Status {labels.get(old, old)} → {labels.get(h.status, h.status)}"
            fields = {"building_id": h.id} if target == "building" else {"installation_order_id": h.id}
            entries.append((h.history_date, Activity(user_id=h.history_user_id, kind=kind, text=text[:400], **fields)))

    first = Activity.objects.order_by("created_at").values_list("created_at", flat=True).first()
    if first:  # the Verlauf already recorded this itself
        entries = [e for e in entries if e[0] < first]
    entries.sort(key=lambda e: e[0])
    for when, activity in entries:
        activity.save()
        Activity.objects.filter(pk=activity.pk).update(created_at=when)  # the real time, not "now"
    return len(entries)
