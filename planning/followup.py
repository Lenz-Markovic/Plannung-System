"""
🧾 Rückmeldungen - database side of rules/followup.py: the office worklist after the visits.

Everything is worked out on every call (nothing extra is stored): reported visits, past stops
nobody reported (❓), ⚠ problems from the phone. The actions call the existing services, which
check their own permissions and write the Verlauf.
"""

import datetime
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db.models import Max
from django.utils import timezone

from .models import StopKind, TourStop, Visit
from .rules import followup as rules
from .rules.visits import COMPLETE, attempt_in_series
from .visits import _info, _planned_dates


@dataclass
class FollowUp:
    state: str
    date: datetime.date
    people: str
    kind: str                      # "reading" / "installation"
    building: object = None
    order: object = None
    visit: object = None
    stop: object = None            # a stop without Ergebnis (❓)
    note: object = None            # the ⚠ note of a problem-only entry
    problems: list = field(default_factory=list)
    wishes: list = field(default_factory=list)
    storno: bool = False
    proposal: str = ""
    planned_on: datetime.date = None
    planned_attempt: int = 0
    deadline: object = None
    position: int = 0
    # set per user by decorate()
    step: str = ""
    primary: str = ""
    hints: list = field(default_factory=list)
    quiet: bool = False
    can_release: bool = False
    can_rework: bool = False
    can_accept: bool = False
    waiting: str = ""              # side panel: "seit 3 Tagen"
    overdue: bool = False

    @property
    def target(self):
        return self.building if self.kind == "reading" and self.building else (self.order or self.building)

    @property
    def target_key(self):
        return "auftrag" if self.object_key[0] == "order" else "liegenschaft"

    @property
    def object_key(self):
        if self.kind == "installation" and self.order is not None:
            return ("order", self.order.pk)
        return ("building", self.building.pk) if self.building is not None else ("order", self.order.pk)

    @property
    def key(self):
        if self.visit is not None:
            return f"v{self.visit.pk}"
        if self.stop is not None:
            return f"s{self.stop.pk}"
        return f"n{self.note.pk}"

    @property
    def ref(self):
        return f"AZ {self.building.file_number}" if self.object_key[0] == "building" else f"RE {self.order.re_number}"

    @property
    def has_problem(self):
        return bool(self.problems)

    @property
    def has_documents(self):
        return bool(self.building is not None and getattr(self.building, "cost_documents", None))

    @property
    def has_wish(self):
        return bool(self.wishes)

    @property
    def is_open(self):
        return rules.is_open(self.state, self.has_problem)


def _key_of_stop(stop):
    if stop.kind == StopKind.INSTALLATION and stop.installation_order_id:
        return ("order", stop.installation_order_id)
    return ("building", stop.building_id) if stop.building_id else ("order", stop.installation_order_id)


def _object_done(entry):
    if entry.object_key[0] == "building":
        return entry.building.status == "released"
    return entry.order.status == "done"


def followup_entries(today, only=None):
    """All entries (every state), sorted by day, people, position. only=("building", id)/("order", id)."""
    from buildings.models import Building, InstallationOrder
    from documents.rules import deadline_info
    from journal.models import Note, NoteKind
    from journal.notes import notes_by_object

    visits = Visit.objects.select_related(
        "building__property_manager", "building__cost_documents", "building__proposed_status_by",
        "installation_order__building", "reported_by", "closed_by", "stop__tour").order_by("date", "pk")
    missing_stops = (TourStop.objects.filter(done_at__isnull=True, outcome="",
                                             tour__date__lt=today,
                                             tour__date__gte=today - datetime.timedelta(days=rules.MISSING_LOOKBACK_DAYS))
                     .exclude(kind=StopKind.HELP)
                     .select_related("tour__employee", "building__cost_documents", "building__property_manager",
                                     "installation_order__building")
                     .prefetch_related("tour__team"))
    problems = Note.objects.filter(kind=NoteKind.PROBLEM, resolved_at=None).select_related("author", "building",
                                                                                            "installation_order")
    if only:
        kind, pk = only
        if kind == "building":
            visits, problems = visits.filter(building_id=pk), problems.filter(building_id=pk)
            missing_stops = missing_stops.filter(building_id=pk).exclude(kind=StopKind.INSTALLATION)
        else:
            visits, problems = visits.filter(installation_order_id=pk), problems.filter(installation_order_id=pk)
            missing_stops = missing_stops.filter(installation_order_id=pk, kind=StopKind.INSTALLATION)

    by_object = {}
    for visit in visits:
        key = ("order", visit.installation_order_id) if visit.installation_order_id else ("building", visit.building_id)
        by_object.setdefault(key, []).append(visit)
    missing_stops = list(missing_stops)
    building_ids = {k[1] for k in by_object if k[0] == "building"} | {s.building_id for s in missing_stops
                                                                      if _key_of_stop(s)[0] == "building"}
    order_ids = {k[1] for k in by_object if k[0] == "order"} | {s.installation_order_id for s in missing_stops
                                                                if _key_of_stop(s)[0] == "order"}
    planned = _planned_dates(building_ids, order_ids)

    entries = []
    for key, items in by_object.items():
        dates = planned.get(key, [])
        for index, visit in enumerate(items):
            entry = FollowUp(state="", date=visit.date, people=visit.people, kind=visit.kind if visit.kind != "help" else "reading",
                             building=visit.building if visit.building_id else (visit.installation_order.building
                                                                                 if visit.installation_order_id else None),
                             order=visit.installation_order, visit=visit,
                             position=visit.stop.position if visit.stop_id else 999)
            if visit.installation_order_id:
                entry.kind = "installation"
            later = [d for d in dates if d >= visit.date]
            entry.planned_on = min(later) if later else None
            entry.planned_attempt = attempt_in_series([_info(v) for v in items[:index + 1]]) if later else 0
            entry.state = rules.office_state(visit.outcome, visit.closed_at is not None, index == len(items) - 1,
                                             bool(later), _object_done(entry))
            entries.append(entry)

    for stop in missing_stops:
        key = _key_of_stop(stop)
        entry = FollowUp(state=rules.MISSING, date=stop.tour.date, people=stop.tour.people_label,
                         kind="installation" if key[0] == "order" else "reading",
                         building=stop.building if stop.building_id else (stop.installation_order.building
                                                                          if stop.installation_order_id else None),
                         order=stop.installation_order, stop=stop, position=stop.position)
        later_planned = sum(1 for d in planned.get(key, []) if d >= stop.tour.date) > 1  # the stop itself is one
        later_visit = any(v.date >= stop.tour.date for v in by_object.get(key, []))
        if rules.missing_report(stop.tour.date, today, later_planned=later_planned, later_visit=later_visit,
                                object_done=_object_done(entry)):
            entries.append(entry)

    # open notes of the objects: problems keep the entry open, wishes and Storno are shown
    newest = {}
    for entry in entries:
        current = newest.get(entry.object_key)
        if current is None or (entry.date, entry.position) >= (current.date, current.position):
            newest[entry.object_key] = entry
    found = notes_by_object({k[1] for k in newest if k[0] == "building"}, {k[1] for k in newest if k[0] == "order"})
    for key, entry in newest.items():
        for note in found.get(key, []):
            if note.kind == NoteKind.PROBLEM:
                entry.problems.append(note)
            elif note.kind == NoteKind.WISH:
                entry.wishes.append(note)
            elif note.kind == NoteKind.STORNO:
                entry.storno = True
        if key[0] == "building" and entry.building is not None:
            entry.proposal = entry.building.proposed_status
    for note in problems:
        key = ("order", note.installation_order_id) if note.installation_order_id else ("building", note.building_id)
        if key in newest:
            continue
        entry = FollowUp(state=rules.PROBLEM, date=timezone.localdate(note.created_at), people=str(note.author or "–"),
                         kind="installation" if key[0] == "order" else "reading", building=note.building,
                         order=note.installation_order, note=note, problems=[note])
        if entry.building is None and entry.order is not None:
            entry.building = entry.order.building
        entries.append(entry)
        newest[key] = entry

    for entry in entries:
        receipt = getattr(entry.building, "cost_documents", None) if entry.building is not None else None
        if receipt is not None and entry.object_key[0] == "building":
            entry.deadline = deadline_info(receipt.deadline_start, entry.building.status == "released", today)
    entries.sort(key=lambda e: (e.date, e.people, e.position, e.key))
    return entries


def decorate(entries, user, today=None):
    """Per user: the next step, the main button, hints, what may be clicked."""
    from buildings.rules.status import can_change_status
    from buildings.services import open_visit

    perms = user.get_all_permissions()
    today = today or timezone.localdate()
    for e in entries:
        status = e.building.status if e.building is not None else ""
        reading = e.object_key[0] == "building"
        e.can_release = (reading and status != "released" and can_change_status(perms, status, "released")
                         and open_visit(e.building) is None)   # never while flats are still open
        e.can_rework = reading and status == "open" and can_change_status(perms, "open", "rework")
        e.can_accept = reading and bool(e.proposal) and can_change_status(perms, status, e.proposal)
        e.step, e.primary = rules.next_step(
            e.state, e.kind, has_problem=e.has_problem, has_documents=e.has_documents, building_status=status,
            has_wish=e.has_wish, can_plan="planning.add_tour" in perms, can_process="planning.process_visit" in perms,
            can_release=e.can_release, can_finish_order="buildings.change_installationorder" in perms,
            can_resolve="journal.change_note" in perms)
        outcome = e.visit.outcome if e.visit else ""
        attempt = e.visit.attempt if e.visit else 1
        e.hints = rules.hints(e.state, e.kind, outcome, attempt, has_documents=e.has_documents, storno=e.storno,
                              planned_on=e.planned_on, today=today)
        e.quiet = rules.is_quiet(e.state, e.kind, e.visit.note if e.visit else "", has_problem=e.has_problem,
                                 has_proposal=bool(e.proposal), has_documents=e.has_documents, building_status=status)
    return entries


def filter_entries(entries, *, day=None, kind="", query=""):
    words = query.lower().split()
    shown = []
    for e in entries:
        if day and e.date != day:
            continue
        if kind and e.kind != kind:
            continue
        if words:
            target = e.target
            text = " ".join(str(x) for x in (
                getattr(e.building, "file_number", ""), getattr(e.order, "re_number", ""),
                getattr(target, "street", ""), getattr(target, "city", ""), e.people)).lower()
            if not all(w in text for w in words):
                continue
        shown.append(e)
    return shown


def group_days(shown, filtered, newest_first=False):
    """[{date, persons: [(people, [entries])], total, open, quiet}] - totals from all entries of that day."""
    days = {}
    for e in shown:
        days.setdefault(e.date, {}).setdefault(e.people, []).append(e)
    result = []
    for date in sorted(days, reverse=newest_first):
        of_day = [e for e in filtered if e.date == date]
        result.append({
            "date": date, "persons": sorted(days[date].items()), "total": len(of_day),
            "open": sum(1 for e in of_day if e.is_open), "quiet": sum(1 for e in of_day if e.quiet),
        })
    return result


def counts(entries):
    return rules.count_filters([(e.state, e.has_problem) for e in entries])


def open_count(today):
    return sum(1 for e in followup_entries(today) if e.is_open)


def stamp():
    """Changes when new field reports arrive (not when the office saves)."""
    from journal.models import Note, NoteKind

    reports = Visit.objects.filter(entered_by_office=False).aggregate(n=Max("pk"))["n"]
    changed = Visit.objects.filter(entered_by_office=False).aggregate(t=Max("reported_at"))["t"]
    problems = Note.objects.filter(kind=NoteKind.PROBLEM).aggregate(n=Max("pk"))["n"]
    return f"{reports}.{changed.timestamp() if changed else 0}.{problems}"


def entry(key, user, today):
    """One decorated entry by its key ("v12" visit, "s34" stop without Ergebnis, "n56" problem note)."""
    from journal.models import Note

    kind, pk = key[0], int(key[1:])
    obj = {"v": Visit, "s": TourStop, "n": Note}[kind].objects.filter(pk=pk).first()
    if obj is None:
        return None
    if kind == "v":
        only = ("order", obj.installation_order_id) if obj.installation_order_id else ("building", obj.building_id)
    elif kind == "s":
        only = _key_of_stop(obj)
    else:
        only = ("order", obj.installation_order_id) if obj.installation_order_id else ("building", obj.building_id)
    found = [e for e in followup_entries(today, only=only) if e.key == key]
    if not found and kind == "n":  # the problem now sits on the newest entry of its object
        found = [e for e in followup_entries(today, only=only) if any(n.pk == pk for n in e.problems)]
    return decorate(found, user, today)[0] if found else None


def entries_of_object(only, user, today):
    return decorate(followup_entries(today, only=only), user, today)


def act(e, action, user, data):
    """Do what a button says. Returns (message, HX events). Raises ValidationError / PermissionDenied."""
    from buildings.orders import update_order
    from buildings.services import accept_proposal, change_status
    from journal import notes as journal_notes
    from journal.models import NoteKind

    from . import visits

    if action in ("release", "rework", "accept"):
        if e.building is None or e.object_key[0] != "building":
            raise ValidationError("Nur für Ablesungen.")
        if action == "accept":
            accept_proposal(e.building, user)
            return f"Vorschlag übernommen · {e.ref}", "buildings-changed, deadlines-changed"
        change_status(e.building, "released" if action == "release" else "rework", user)
        return (f"Freigegeben · {e.ref}" if action == "release" else f"Nacharbeit gesetzt · {e.ref}",
                "buildings-changed, deadlines-changed")
    if action in ("check", "close", "reopen"):
        if e.visit is None:
            raise ValidationError("Zu diesem Eintrag gibt es noch kein Ergebnis.")
        visits.close(e.visit, user, action != "reopen", data.get("closed_note", ""))
        message = {"check": "Geprüft", "close": "Abgeschlossen – kein Nachtermin nötig", "reopen": "Wieder offen"}[action]
        return f"{message} · {e.ref}", "buildings-changed, orders-changed"
    if action == "finish_order":
        if e.order is None:
            raise ValidationError("Nur für Montageaufträge.")
        from django.http import QueryDict
        update_order(e.order, QueryDict("status=done"), user)
        return f"Auftrag erledigt · {e.ref}", "orders-changed, conflicts-changed"
    if action == "wish":
        building = e.building if e.object_key[0] == "building" else None
        journal_notes.add_note(user, data.get("wish", ""), NoteKind.WISH, building=building,
                               order=e.order if building is None else None)
        return "Terminwunsch an Disposition", "notes-changed"
    if action == "resolve":
        note = next((n for n in e.problems if str(n.pk) == str(data.get("note", ""))), None)
        if note is None:
            raise ValidationError("Dieses Problem gibt es hier nicht (mehr).")
        journal_notes.set_resolved(note, user, True, answer=data.get("answer", ""))
        return "Problem erledigt", "notes-changed"
    raise ValidationError("Unbekannte Aktion.")


def check_quiet(day, user, today):
    """'✓ n unauffällige abhaken': plain ✓ readings of that day (checked again here) become 'geprüft'."""
    from . import visits

    if not user.has_perm("planning.process_visit"):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Rückmeldungen bearbeiten darf deine Rolle nicht.")
    done = 0
    for e in decorate([e for e in followup_entries(today) if e.date == day], user, today):
        if e.quiet and e.visit is not None and e.visit.outcome == COMPLETE:
            visits.close(e.visit, user, True)
            done += 1
    return done


def panel_sections(user, today, sort="dringend", kind=""):
    """The 🧾 side panel: open entries in sections, most urgent section first, sorted inside."""
    entries = decorate(filter_entries(followup_entries(today), kind=kind), user, today)
    sections = {state: [] for state in rules.URGENCY}
    for e in entries:
        section = rules.section_of(e.state, e.has_problem)
        if section is not None:
            e.waiting = rules.waiting_label(e.date, today)
            e.overdue = rules.is_overdue(rules.CHECK if section == rules.PROBLEM else e.state, e.date, today)
            sections[section].append(e)
    result = []
    for state in rules.URGENCY:
        items = sorted(sections[state], key=lambda e: rules.panel_sort_key(sort, e.date, e.people, e.position))
        result.append({"state": state, "label": rules.SECTION_LABELS[state], "items": items})
    return result


def new_since(since, user):
    """New field reports and ⚠ problems after `since` (not the user's own, not entered in the office):
    [(when, text, key)] newest first - for the pop-ups."""
    from journal.models import Note, NoteKind

    from .rules.visits import OUTCOMES

    found = []
    reports = (Visit.objects.filter(reported_at__gt=since, entered_by_office=False).exclude(reported_by=user)
               .select_related("building", "installation_order", "reported_by"))
    for v in reports.order_by("-reported_at")[:20]:
        target = v.building or v.installation_order
        ref = f"AZ {v.building.file_number}" if v.building_id else f"RE {v.installation_order.re_number}"
        rest = f" – {v.todo}" if v.todo else ""
        found.append((v.reported_at, v.outcome, f"{v.people}: {ref} {target.street} · {OUTCOMES[v.outcome]}{rest}",
                      f"v{v.pk}"))
    problems = (Note.objects.filter(kind=NoteKind.PROBLEM, resolved_at=None, created_at__gt=since).exclude(author=user)
                .select_related("author", "building", "installation_order"))
    for n in problems.order_by("-created_at")[:20]:
        target = n.building or n.installation_order
        found.append((n.created_at, "problem", f"{n.author or '?'}: {target.street} · ⚠ {n.text[:120]}", f"n{n.pk}"))
    found.sort(key=lambda item: item[0], reverse=True)
    return found
