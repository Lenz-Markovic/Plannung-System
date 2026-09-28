from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

# Shown on the start page so everyone can check what their role allows.
PERMISSION_LABELS = [
    ("buildings.view_building", "Liegenschaften und Daten ansehen"),
    ("planning.change_tour", "Fahrpläne erstellen, verschieben, löschen"),
    ("buildings.assign_employee", "Ableser/Monteur zuweisen"),
    ("documents.add_costdocumentreceipt", "Unterlagen-Eingang eintragen"),
    ("buildings.set_status_open_rework", "Status offen / Nacharbeit"),
    ("buildings.propose_status", "Status vorschlagen"),
    ("buildings.release_building", "Status „freigegeben“"),
    ("buildings.change_building", "Hausverwaltung und Notizen pflegen"),
    ("planning.add_field_note", "Notiz vor Ort"),
    ("planning.view_own_tours", "Eigenen Tagesplan sehen"),
    ("planning.mark_stop_done", "Stopp „erledigt“"),
    ("planning.view_reports", "Auswertungen, Zeiten je Ableser"),
]


@login_required
def home(request):
    # Office roles start directly in the building list (like the prototype).
    if request.user.has_perm("buildings.view_building"):
        return redirect("buildings:list")
    permissions = [
        (label, request.user.has_perm(codename)) for codename, label in PERMISSION_LABELS
    ]
    return render(request, "core/home.html", {"permissions": permissions})
