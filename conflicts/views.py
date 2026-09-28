"""
"⚠ Konflikte" page: all stored reading-vs-installation messages
(like the "Montage-Meldungen" card of the prototype).

A dispatcher can accept a conflict knowingly ("bewusst übernehmen", with a
reason); it then moves to the "übernommen" filter and stops counting in the
navigation badge. The rows are recalculated after every planning change
(conflicts/services.py).
"""

from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from . import services
from .models import Conflict, Severity

FILTERS = {
    "offen": Q(acknowledged_at__isnull=True) & ~Q(severity=Severity.INFO),
    "kritisch": Q(acknowledged_at__isnull=True, severity=Severity.CRITICAL),
    "pruefen": Q(acknowledged_at__isnull=True, severity=Severity.WARNING),
    "hinweise": Q(acknowledged_at__isnull=True, severity=Severity.INFO),
    "uebernommen": Q(acknowledged_at__isnull=False),
    "alle": Q(),
}
RANK = Case(When(severity=Severity.CRITICAL, then=Value(0)), When(severity=Severity.WARNING, then=Value(1)),
            When(severity=Severity.HINT, then=Value(2)), default=Value(3), output_field=IntegerField())


def open_count():
    """Number for the badge in the navigation: open conflicts + things to check."""
    return Conflict.objects.filter(FILTERS["offen"]).count()


def _counts():
    return Conflict.objects.aggregate(
        open=Count("pk", filter=FILTERS["offen"]),
        critical=Count("pk", filter=FILTERS["kritisch"]),
        warning=Count("pk", filter=FILTERS["pruefen"]),
        info=Count("pk", filter=FILTERS["hinweise"]),
        acknowledged=Count("pk", filter=FILTERS["uebernommen"]),
        all=Count("pk"),
    )


@permission_required("conflicts.view_conflict", raise_exception=True)
def conflict_list(request):
    chosen = request.GET.get("f") if request.GET.get("f") in FILTERS else "offen"
    conflicts = (Conflict.objects.filter(FILTERS[chosen])
                 .select_related("building", "installation_order", "stop__tour__employee",
                                 "other_stop__tour__employee", "acknowledged_by")
                 .annotate(rank=RANK).order_by("rank", "stop__tour__date", "building__file_number"))
    query = request.GET.get("q", "").strip()
    if query:
        conflicts = conflicts.filter(Q(building__file_number__icontains=query) | Q(building__street__icontains=query)
                                     | Q(building__city__icontains=query) | Q(installation_order__re_number__icontains=query)
                                     | Q(message__icontains=query))
    context = {"conflicts": conflicts[:300], "total": conflicts.count(), "counts": _counts(), "chosen": chosen}
    template = "conflicts/_entries.html" if request.htmx_target == "conflict-entries" else "conflicts/list.html"
    return render(request, template, context)


@require_POST
@permission_required("conflicts.acknowledge_conflict", raise_exception=True)
def conflict_acknowledge(request, pk):
    """Accept knowingly (with a reason) or open again ("wieder öffnen")."""
    conflict = get_object_or_404(Conflict.objects.select_related("building", "installation_order"), pk=pk)
    error = ""
    if request.POST.get("reopen"):
        services.reopen(conflict, request.user)
        message = "Konflikt wieder offen"
    else:
        try:
            services.acknowledge(conflict, request.user, request.POST.get("note", ""))
            message = "Bewusst übernommen"
        except ValidationError as problem:
            error, message = problem.messages[0], problem.messages[0]
    response = render(request, "conflicts/_entry.html", {"c": conflict, "message": message, "error": error})
    response["HX-Trigger"] = "conflicts-changed"  # the badge in the navigation counts again
    return response


@permission_required("conflicts.view_conflict", raise_exception=True)
def conflict_badge(request):
    return render(request, "conflicts/_badge.html", {"count": open_count()})
