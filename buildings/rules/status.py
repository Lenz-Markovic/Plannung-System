"""
Status workflow of a building: offen / Nacharbeit / freigegeben.

Who may do what (spec section 5, "Rollen und Rechte"):
- "offen" <-> "Nacharbeit": permission buildings.set_status_open_rework
  (Admin, Disposition, Sachbearbeitung)
- anything involving "freigegeben" (setting it, or taking it back):
  permission buildings.release_building (Admin, Sachbearbeitung)
- readers may only PROPOSE a status (buildings.propose_status)
- never "freigegeben" while flats are still open: the last visit was teilweise / nicht erledigt and the
  office has not closed it (special cases: close it in Rückmeldungen with a reason first)

Pure functions: they get the user's permissions as a set of strings,
so they can be tested without a database.
"""

from buildings.models import BuildingStatus

OPEN_REWORK_PERMISSION = "buildings.set_status_open_rework"
RELEASE_PERMISSION = "buildings.release_building"
OPEN_OUTCOMES = ("partial", "absent")   # the last visit left something to do


def release_blocked(last_outcome, last_closed):
    """True while the last visit left flats open and nobody in the office closed it."""
    return last_outcome in OPEN_OUTCOMES and not last_closed


def release_blocked_message(todo=""):
    what = f" ({todo})" if todo else ""
    return (f"Freigeben geht noch nicht: beim letzten Termin ist noch etwas offen{what}. Erst den Nachtermin erledigen. "
            "Sonderfall: in 🧾 Rückmeldungen „abschließen – kein Nachtermin nötig“ mit Grund, danach freigeben.")


def can_change_status(permissions, current, new):
    """May a user with these permissions change the status from current to new?"""
    if current == new:
        return True
    if BuildingStatus.RELEASED in (current, new):
        return RELEASE_PERMISSION in permissions
    return OPEN_REWORK_PERMISSION in permissions


def status_options(permissions, current, blocked=False):
    """All statuses with a flag whether the user may choose it (for the dropdown).
    blocked: flats are still open, so "freigegeben" can't be chosen (unless it already is)."""
    return [
        (value, label, can_change_status(permissions, current, value)
         and not (blocked and value == BuildingStatus.RELEASED and current != value))
        for value, label in BuildingStatus.choices
    ]
