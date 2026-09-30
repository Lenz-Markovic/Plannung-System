"""
Write operations and queries around the 14-day deadline.

The decisions are made by the pure functions in documents/rules.py; this
module only loads and saves the data.
"""

from django.db.models import OuterRef, Subquery
from django.utils import timezone

from buildings.models import BuildingStatus
from journal.activity import record
from journal.models import ActivityKind
from planning.models import StopKind, TourStop
from planning.queries import current_first

from .models import CostDocumentReceipt
from .rules import OVERDUE, DeadlineTracking, deadline_info, track_deadline, warning_colour


def planned_reading_date(building):
    """The current reading appointment (the next open one; a Nachtermin restarts the deadline). None if not planned."""
    stop = current_first(TourStop.objects.filter(building=building, kind=StopKind.READING)).select_related("tour").first()
    return stop.tour.date if stop else None


def _apply_tracking(receipt, status, planned_date, today):
    """Restart the deadline if needed. Returns True if something changed."""
    old = DeadlineTracking(receipt.deadline_start, receipt.reset_reason,
                           receipt.last_seen_status, receipt.last_seen_planned_date)
    new = track_deadline(old, status, planned_date, today)
    if new == old:
        return False
    receipt.deadline_start = new.deadline_start
    receipt.reset_reason = new.reset_reason
    receipt.last_seen_status = new.last_seen_status
    receipt.last_seen_planned_date = new.last_seen_planned_date
    receipt.save()
    return True


def refresh_deadline(building, today=None):
    """Call after every status change or (re)planning of a building."""
    receipt = CostDocumentReceipt.objects.filter(building=building).first()
    if receipt:
        _apply_tracking(receipt, building.status, planned_reading_date(building), today or timezone.localdate())


def set_received_on(building, received_on, user):
    """Enter, change or remove the date the cost documents arrived.

    A new date starts a fresh 14-day deadline from that date.
    """
    receipt = CostDocumentReceipt.objects.filter(building=building).first()
    if received_on is None:
        if receipt:
            receipt.delete()  # kept in the change history
            record(user, ActivityKind.DOCUMENTS, f"AZ {building.file_number} {building.street}: Unterlagen-Eingang entfernt",
                   building=building)
        return None
    if receipt and receipt.received_on == received_on:
        return receipt
    receipt = receipt or CostDocumentReceipt(building=building)
    receipt.received_on = received_on
    receipt.deadline_start = received_on
    receipt.reset_reason = ""
    receipt.last_seen_status = building.status
    receipt.last_seen_planned_date = planned_reading_date(building)
    receipt.entered_by = user
    receipt.save()
    record(user, ActivityKind.DOCUMENTS, f"AZ {building.file_number} {building.street}: Unterlagen erhalten am "
           f"{received_on:%d.%m.%Y}", building=building)
    return receipt


class DeadlineEntry:
    """One building with received documents, for the list and the warning."""

    def __init__(self, receipt, info):
        self.receipt = receipt
        self.building = receipt.building
        self.info = info
        self.planned_date = receipt.planned_date
        self.planned_reader = receipt.planned_reader
        self.colour = warning_colour(self.planned_date)


def deadline_entries(today=None, refresh=True):
    """All buildings with received documents and their deadline state.

    refresh=True first restarts deadlines where the status or appointment
    changed (like prioSync() in the prototype, which ran every 3 seconds).
    """
    today = today or timezone.localdate()
    reading = current_first(TourStop.objects.filter(building=OuterRef("building"), kind=StopKind.READING))
    receipts = (
        CostDocumentReceipt.objects.select_related("building", "building__property_manager")
        .annotate(planned_date=Subquery(reading.values("tour__date")[:1]),
                  planned_reader=Subquery(reading.values("tour__employee__short_name")[:1]))
    )
    entries = []
    for receipt in receipts:
        if refresh:
            _apply_tracking(receipt, receipt.building.status, receipt.planned_date, today)
        info = deadline_info(receipt.deadline_start, receipt.building.status == BuildingStatus.RELEASED, today)
        entries.append(DeadlineEntry(receipt, info))
    return entries


def overdue_entries(today=None):
    """Buildings whose deadline has expired, most overdue first."""
    overdue = [entry for entry in deadline_entries(today) if entry.info.state == OVERDUE]
    return sorted(overdue, key=lambda entry: (entry.info.days_left, entry.building.file_number))
