"""Shared query helpers for tour stops."""

from django.db.models import Case, F, IntegerField, Q, Value, When

# a stop nobody reported yet (not done, no Ergebnis) - the "current" appointment
OPEN_STOP = Q(done_at__isnull=True, outcome="")


def current_first(stops):
    """Order stops so the CURRENT appointment comes first: the next open stop (earliest day),
    and only if there is none, the last visited one (latest day).

    A visited stop stays as history (e.g. 'teilweise' on 16.11.); the Nachtermin on 08.12. is the
    appointment that counts for conflicts, deadlines, the lists and the material budget.
    """
    return stops.annotate(
        _visited=Case(When(OPEN_STOP, then=Value(0)), default=Value(1), output_field=IntegerField()),
        _open_day=Case(When(OPEN_STOP, then=F("tour__date"))),
        _done_day=Case(When(~OPEN_STOP, then=F("tour__date"))),
    ).order_by("_visited", F("_open_day").asc(nulls_last=True), F("_done_day").desc(nulls_last=True), "start_time", "pk")
