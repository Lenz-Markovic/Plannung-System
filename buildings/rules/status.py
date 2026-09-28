"""
Status workflow of a building: offen / Nacharbeit / freigegeben.

Who may do what (spec section 5, "Rollen und Rechte"):
- "offen" <-> "Nacharbeit": permission buildings.set_status_open_rework
  (Admin, Disposition, Sachbearbeitung)
- anything involving "freigegeben" (setting it, or taking it back):
  permission buildings.release_building (Admin, Sachbearbeitung)
- readers may only PROPOSE a status (buildings.propose_status)

Pure functions: they get the user's permissions as a set of strings,
so they can be tested without a database.
"""

from buildings.models import BuildingStatus

OPEN_REWORK_PERMISSION = "buildings.set_status_open_rework"
RELEASE_PERMISSION = "buildings.release_building"


def can_change_status(permissions, current, new):
    """May a user with these permissions change the status from current to new?"""
    if current == new:
        return True
    if BuildingStatus.RELEASED in (current, new):
        return RELEASE_PERMISSION in permissions
    return OPEN_REWORK_PERMISSION in permissions


def status_options(permissions, current):
    """All statuses with a flag whether the user may choose it (for the dropdown)."""
    return [
        (value, label, can_change_status(permissions, current, value))
        for value, label in BuildingStatus.choices
    ]
