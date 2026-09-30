"""
🧾 Rückmeldungen: what the office still has to do after the visits (pure functions, no database).

Every entry of the office worklist is one reported visit, one past stop nobody reported (❓),
or a ⚠ problem from the phone on an object without a visit. Each entry has exactly one state
and one "Nächster Schritt". Nothing is decided automatically here - only worked out.
"""

import datetime

from .visits import ABSENT, COMPLETE, PARTIAL

CHECK, REVISIT, PLANNED, DONE, LATER, MISSING, PROBLEM = "check", "revisit", "planned", "done", "later", "missing", "problem"
STATE_LABELS = {
    CHECK: "✓ fertig – im Büro prüfen",
    REVISIT: "🔁 Nachtermin nötig",
    PLANNED: "📅 Nachtermin geplant",
    DONE: "✓ erledigt",
    LATER: "↪ später wieder besucht",
    MISSING: "❓ keine Rückmeldung",
    PROBLEM: "⚠ Problem vor Ort",
}
OPEN_STATES = frozenset({CHECK, REVISIT, MISSING, PROBLEM})
MISSING_LOOKBACK_DAYS = 30        # unreported stops of the last 30 days are listed as ❓
ESTIMATE_HINT_FROM_ATTEMPT = 2    # from the 2nd unsuccessful reading: "Schätzung prüfen?"
MAX_NOTE = 300
CLOSE_PICKS = ["telefonisch geklärt", "Werte geschätzt – kein Zutritt", "Selbstablesung (Mieter / Hausverwaltung)",
               "Storno – nicht mehr nötig", "Hausverwaltung klärt"]
FILTERS = [("offen", "offen"), ("pruefen", "✓ prüfen"), ("nachtermin", "🔁 Nachtermin nötig"),
           ("ohne", "❓ keine Rückmeldung"), ("probleme", "⚠ Probleme"), ("geplant", "📅 Nachtermin geplant"),
           ("erledigt", "✓ erledigt"), ("alle", "alle")]
FILTER_KEYS = [key for key, _ in FILTERS]


def office_state(outcome, closed, is_last, planned_again, object_done):
    """What the office still has to do with ONE visit (checked in this order)."""
    if closed:
        return DONE
    if not is_last:
        return LATER  # a newer visit of the same object took over
    if outcome == COMPLETE:
        return DONE if object_done else CHECK  # a ✓ is never "Nachtermin geplant"
    # ◐ / ✗: exactly needs_revisit() - the page, the tiles and the "Nachtermin nötig" filter agree
    return PLANNED if planned_again else REVISIT


def missing_report(tour_date, today, *, later_planned=False, later_visit=False, object_done=False,
                   lookback=MISSING_LOOKBACK_DAYS):
    """A past stop without Ergebnis (❓): only the last `lookback` days, not today (still being worked on),
    and not when the object was planned again, visited later or is done anyway."""
    if not (today - datetime.timedelta(days=lookback) <= tour_date < today):
        return False
    return not (later_planned or later_visit or object_done)


def is_open(state, has_problem=False):
    return state in OPEN_STATES or has_problem


def chosen_filter(value, day_chosen):
    if value in FILTER_KEYS:
        return value
    return "alle" if day_chosen else "offen"


def in_filter(chosen, state, has_problem=False):
    if chosen == "alle":
        return True
    if chosen == "pruefen":
        return state == CHECK
    if chosen == "nachtermin":
        return state == REVISIT
    if chosen == "ohne":
        return state == MISSING
    if chosen == "probleme":
        return has_problem or state == PROBLEM
    if chosen == "geplant":
        return state == PLANNED
    if chosen == "erledigt":
        return state in (DONE, LATER) and not has_problem
    return is_open(state, has_problem)  # "offen" and unknown keys


def count_filters(items):
    """items: (state, has_problem) pairs -> {filter key: number}."""
    return {key: sum(1 for state, problem in items if in_filter(key, state, problem)) for key in FILTER_KEYS}


def next_step(state, kind, *, has_problem=False, has_documents=False, building_status="", has_wish=False,
              can_plan=False, can_process=False, can_release=False, can_finish_order=False, can_resolve=False):
    """(text, primary button) - primary is "" when this user has no button for it."""
    if has_problem or state == PROBLEM:
        return "⚠ Problem vor Ort ansehen, klären und „✓ Problem erledigt“", "resolve" if can_resolve else ""
    if state == MISSING:
        return "Beim Ableser/Monteur nachfragen und das Ergebnis nachtragen", "report" if can_process else ""
    if state == CHECK and kind == "reading":
        if building_status == "rework":
            return "Nacharbeit klären – danach „✓ geprüft“", "check" if can_process else ""
        if has_documents and can_release:
            return "Werte prüfen und freigeben", "release"
        if has_documents:
            return "Werte prüfen – freigeben macht die Sachbearbeitung", ""
        return ("Werte prüfen, dann „✓ geprüft“ – freigeben, sobald die Unterlagen da sind",
                "check" if can_process else "")
    if state == CHECK:
        if can_finish_order:
            return "Montage prüfen und den Auftrag auf „Erledigt“ setzen", "finish_order"
        return "Auftrag auf „Erledigt“ setzen – macht die Disposition", ""
    if state == REVISIT:
        if can_plan:
            return "Nachtermin planen: vormerken → „🗺 Fahrplan erstellen“ – oder abschließen, wenn keiner nötig ist", "plan"
        if has_wish:
            return "Terminwunsch liegt bei der Disposition – sie plant den Nachtermin", ""
        if can_process:
            return ("Mit Mieter / Hausverwaltung klären, dann „📅 Terminwunsch an Disposition“ – oder abschließen, "
                    "wenn keiner nötig ist", "wish")
        return "Den Nachtermin plant die Disposition", ""
    if state == PLANNED:
        return "Nachtermin ist geplant – nichts zu tun", ""
    return "", ""


def hints(state, kind, outcome, attempt, *, has_documents=False, storno=False, planned_on=None, today=None):
    """Hints only - nothing is decided automatically."""
    found = []
    if state == REVISIT and kind == "reading" and outcome == ABSENT and attempt >= ESTIMATE_HINT_FROM_ATTEMPT:
        found.append(f"Schon der {attempt}. Termin ohne Erfolg – Schätzung prüfen?")
    if state == REVISIT and storno:
        found.append("⛔ Storno offen – wird nicht zur Planung vorgeschlagen; abschließen mit Grund „Storno“?")
    if state == CHECK and kind == "reading" and not has_documents:
        found.append("📄 Unterlagen (Kosten) fehlen noch")
    if state == PLANNED and planned_on and today and planned_on < today:
        found.append(f"Der geplante Termin {planned_on:%d.%m.} ist vorbei, aber ohne Rückmeldung – bitte nachfragen")
    return found


def is_quiet(state, kind, note, *, has_problem=False, has_proposal=False, has_documents=False, building_status="open"):
    """A plain ✓ reading with nothing to look at (for "✓ n unauffällige abhaken")."""
    return (state == CHECK and kind == "reading" and not (note or "").strip() and not has_problem
            and not has_proposal and not has_documents and building_status == "open")


def close_problems(outcome, note):
    """What is missing when the office closes a visit? [] = ok."""
    note = (note or "").strip()
    if len(note) > MAX_NOTE:
        return [f"Höchstens {MAX_NOTE} Zeichen."]
    if outcome in (PARTIAL, ABSENT) and not note:
        return ["Bitte kurz eintragen, warum kein Nachtermin nötig ist (z. B. „telefonisch geklärt“)."]
    return []


# --- the 🧾 side panel: sorted, most urgent first ------------------------------------------------

# what needs the office first: a problem on site, nobody reported, a Nachtermin to organise, a ✓ to check
URGENCY = [PROBLEM, MISSING, REVISIT, CHECK]
SECTION_LABELS = {
    PROBLEM: "⚠ Probleme vor Ort",
    MISSING: "❓ Keine Rückmeldung",
    REVISIT: "🔁 Nachtermin nötig",
    CHECK: "✓ Fertig – im Büro prüfen",
}
SORTS = [("dringend", "dringend zuerst"), ("alt", "am längsten offen zuerst"), ("neu", "neueste zuerst"),
         ("person", "nach Person")]
SORT_KEYS = [key for key, _ in SORTS]
POPUP_MAX = 4  # more new reports at once: "+ n weitere"


def section_of(state, has_problem=False):
    """The panel section of an open entry (a problem wins), None when nothing is to do."""
    if has_problem or state == PROBLEM:
        return PROBLEM
    return state if state in URGENCY else None


def chosen_sort(value):
    return value if value in SORT_KEYS else "dringend"


def panel_sort_key(sort, date, people, position):
    """Sort key inside a section. dringend = waiting longest first, like alt (the section order does the rest)."""
    if sort == "neu":
        return (-date.toordinal(), people, position)
    if sort == "person":
        return (people.lower(), date.toordinal(), position)
    return (date.toordinal(), people, position)


def waiting_label(date, today):
    days = (today - date).days
    if days <= 0:
        return "heute" if days == 0 else f"in {-days} Tagen"
    return "gestern" if days == 1 else f"seit {days} Tagen"


def is_overdue(state, date, today, limit=3):
    """Open for more than `limit` days: shown in red in the panel."""
    return state in OPEN_STATES and (today - date).days > limit
