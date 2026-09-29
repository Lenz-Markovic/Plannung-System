"""
Roles and their default permissions (spec section 5, "Rollen und Rechte").

Roles are Django groups. This file only defines the *starting point*:
`python manage.py setup_roles` creates the groups and gives them these
permissions. After that, an admin can change the permissions of each role
in the Django admin (spec: "Die Rollen und ihre Rechte müssen im
Admin-Bereich änderbar sein").

Permissions are written as "app_label.codename". Django creates
view_/add_/change_/delete_ for every model automatically; the special ones
(e.g. planning.confirm_tour) are defined in the models' Meta.permissions.
"""

ADMIN = "Admin"
DISPATCHER = "Disposition"
PROCESSING = "Sachbearbeitung"
READER = "Ableser/Monteur"
MANAGEMENT = "Leitung"

ALL_ROLES = [ADMIN, DISPATCHER, PROCESSING, READER, MANAGEMENT]

# Special value: the Admin role gets every permission that exists.
ALL_PERMISSIONS = "__all__"

# "Liegenschaften und Daten ansehen"
VIEW_DATA = [
    "buildings.view_building",
    "buildings.view_propertymanager",
    "buildings.view_installationorder",
    "buildings.view_installationorderitem",
    "buildings.view_devicecategory",
    "planning.view_employee",
    "planning.view_absence",
    "planning.view_tour",
    "planning.view_tourstop",
    "conflicts.view_conflict",
    "documents.view_coversheet",
    "documents.view_costdocumentreceipt",
    "journal.view_note",
]

# "📝 Notizen schreiben" (e.g. Storno from the Terminierung) and mark them erledigt
WRITE_NOTES = [
    "journal.add_note",
    "journal.change_note",
]

# "🕘 Verlauf sehen": who changed what (plans, appointments, printed notices, status)
VIEW_ACTIVITY = [
    "journal.view_activity",
]

# "Fahrpläne erstellen, verschieben, löschen"
EDIT_TOURS = [
    "planning.add_tour",
    "planning.change_tour",
    "planning.delete_tour",
    "planning.confirm_tour",
    "planning.add_tourstop",
    "planning.change_tourstop",
    "planning.delete_tourstop",
    "planning.add_absence",
    "planning.change_absence",
    "planning.delete_absence",
    "conflicts.acknowledge_conflict",
]

# "Unterlagen-Eingang eintragen"
ENTER_DOCUMENTS = [
    "documents.add_costdocumentreceipt",
    "documents.change_costdocumentreceipt",
]

# "Hausverwaltung und Notizen pflegen"
EDIT_PROPERTY_MANAGER_AND_NOTES = [
    "buildings.change_building",
    "buildings.add_propertymanager",
    "buildings.change_propertymanager",
]

# "Eigenen Tagesplan sehen, Stopp erledigt"
OWN_DAY_PLAN = [
    "planning.view_own_tours",
    "planning.mark_stop_done",
]

ROLE_PERMISSIONS = {
    ADMIN: ALL_PERMISSIONS,
    DISPATCHER: [
        *VIEW_DATA,
        *EDIT_TOURS,
        "buildings.assign_employee",
        "buildings.change_installationorder",
        # "○ optional" in the spec: granted by default, can be removed in the admin
        *ENTER_DOCUMENTS,
        "buildings.set_status_open_rework",
        *EDIT_PROPERTY_MANAGER_AND_NOTES,
        *OWN_DAY_PLAN,
        "planning.view_reports",
        *WRITE_NOTES,
        *VIEW_ACTIVITY,
    ],
    PROCESSING: [
        *VIEW_DATA,
        *ENTER_DOCUMENTS,
        "buildings.set_status_open_rework",
        "buildings.release_building",
        *EDIT_PROPERTY_MANAGER_AND_NOTES,
        "planning.view_reports",
        *WRITE_NOTES,
        *VIEW_ACTIVITY,
    ],
    READER: [
        # "nur eigene Termine": no view_building; the views show them only
        # the buildings of their own tours.
        *OWN_DAY_PLAN,
        "buildings.propose_status",  # status: "Vorschlag"
        "planning.add_field_note",  # notes: "Notiz vor Ort"
    ],
    MANAGEMENT: [
        *VIEW_DATA,
        "planning.view_reports",
        *VIEW_ACTIVITY,
    ],
}
