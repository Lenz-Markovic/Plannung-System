"""
Write operations on buildings (called by the views).

Each function checks the permission, saves, and triggers what has to
happen afterwards (e.g. a status change restarts the 14-day deadline).
The change history is written automatically by django-simple-history.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from documents.services import refresh_deadline

from .models import BuildingStatus, PropertyManager
from .rules.access import detect_access
from .rules.status import can_change_status


def apply_access(building):
    """Fill the access fields from reading type and notes (not saved here)."""
    access = detect_access(building.reading_type, building.remark, building.note, building.handwritten_note)
    building.access_apartment = access.apartment
    building.access_room = access.room
    building.access_units = access.units
    building.access_reasons = access.reasons
    building.key_hint = access.key_hint
    building.announcement_hint = access.announcement_hint
    return access


def change_status(building, new_status, user):
    if new_status not in BuildingStatus.values:
        raise ValidationError(f"Unbekannter Status: {new_status}")
    if not can_change_status(user.get_all_permissions(), building.status, new_status):
        raise PermissionDenied("Diesen Status darf deine Rolle nicht setzen.")
    if building.status == new_status:
        return building
    building.status = new_status
    building.status_changed_at = timezone.now()
    # A decision was made, so an open proposal from a reader is done.
    building.proposed_status, building.proposed_status_by, building.proposed_status_at = "", None, None
    building.save()
    refresh_deadline(building)
    return building


def set_note(building, text, user):
    if not user.has_perm("buildings.change_building"):
        raise PermissionDenied("Notizen darf deine Rolle nicht ändern.")
    building.note = text.strip()
    apply_access(building)  # a note like "NE004 manuell ablesen" changes the access
    building.save()
    return building


def set_property_manager(building, name, user):
    """Set the property manager by name; a new name creates a new entry."""
    if not user.has_perm("buildings.change_building"):
        raise PermissionDenied("Die Hausverwaltung darf deine Rolle nicht ändern.")
    name = " ".join(name.split())
    if not name:
        building.property_manager = None
    else:
        manager = PropertyManager.objects.filter(name__iexact=name).first()
        if manager is None:
            if not user.has_perm("buildings.add_propertymanager"):
                raise PermissionDenied("Neue Hausverwaltungen darf deine Rolle nicht anlegen.")
            manager = PropertyManager.objects.create(name=name)
        building.property_manager = manager
    building.save()
    return building


def propose_status(building, new_status, user):
    """A reader suggests a status ("Vorschlag" in the role table); the office decides."""
    if not user.has_perm("buildings.propose_status"):
        raise PermissionDenied("Deine Rolle darf keinen Status vorschlagen.")
    if new_status not in BuildingStatus.values:
        raise ValidationError(f"Unbekannter Status: {new_status}")
    if new_status == building.status:
        building.proposed_status, building.proposed_status_by, building.proposed_status_at = "", None, None
    else:
        building.proposed_status, building.proposed_status_by, building.proposed_status_at = new_status, user, timezone.now()
    building.save()
    return building


def accept_proposal(building, user):
    """Office: take over the reader's suggestion (normal permission rules apply)."""
    if not building.proposed_status:
        return building
    return change_status(building, building.proposed_status, user)
