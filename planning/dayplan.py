"""
Day plan of one reader / installer (mobile view "Mein Tag").

Readers may only work on their OWN stops (spec: "nur eigene Termine");
office roles with planning.view_tour may look at everybody's day.
"""

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from .models import Tour, TourStatus


def may_work_on(user, tour):
    """Own tour, or an office role that may change tours."""
    return tour.employee.user_id == user.pk or user.has_perm("planning.change_tour")


def set_stop_done(stop, user, done):
    if not user.has_perm("planning.mark_stop_done") or not may_work_on(user, stop.tour):
        raise PermissionDenied("Diesen Stopp darfst du nicht als erledigt markieren.")
    stop.done_at, stop.done_by = (timezone.now(), user) if done else (None, None)
    stop.save()
    tour = stop.tour
    all_done = not tour.stops.filter(done_at__isnull=True).exists()
    if all_done and tour.status != TourStatus.DONE:
        tour.status = TourStatus.DONE
        tour.save()
    elif not all_done and tour.status == TourStatus.DONE:
        tour.status = TourStatus.CONFIRMED if tour.confirmed_at else TourStatus.PROVISIONAL
        tour.save()
    return stop


def save_field_note(stop, text, user):
    if not user.has_perm("planning.add_field_note") and not user.has_perm("planning.change_tourstop"):
        raise PermissionDenied("Notizen vor Ort darf deine Rolle nicht erfassen.")
    if not may_work_on(user, stop.tour):
        raise PermissionDenied("Das ist nicht dein Stopp.")
    stop.field_note = text.strip()
    stop.save()
    return stop


def progress(stops):
    done = sum(1 for s in stops if s.done_at)
    return {"done": done, "total": len(stops), "percent": round(done / len(stops) * 100) if stops else 0,
            "next": next((s for s in stops if not s.done_at), None)}


def tour_of(employee, date):
    return Tour.objects.filter(employee=employee, date=date).select_related("employee").first()
