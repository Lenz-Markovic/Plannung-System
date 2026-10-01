"""📄 Aushänge & Ankündigungen: how the tenants of every coming appointment are told, and the
Aushang-Fahrten (planned like a reading - planning:dialog with the appointments ticked here)."""

from django.contrib.auth.decorators import permission_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from planning import services
from planning.models import TourStop

from . import notice_rules as rules
from . import notices

VIEW = "planning.view_tour"


def may_edit(user):
    """Planners (change plans) and the office that works through the Rückmeldungen (Sachbearbeitung)."""
    return user.has_perm("planning.change_tour") or user.has_perm("planning.process_visit")


def edit_required(view):
    from functools import wraps

    from django.core.exceptions import PermissionDenied

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not may_edit(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def _bar(session):
    return {"notice_count": len(services.get_notice_selection(session))}


def _row_context(request, row, message="", error=False):
    html = render_to_string("documents/_announce_row.html", {"r": row, "channels": rules.CHANNELS, "scopes": rules.SCOPES,
                                                             "today": timezone.localdate(), "may_edit": True}, request=request)
    if message:
        html += render_to_string("core/_toast.html", {"message": message, "error": error}, request=request)
    return HttpResponse(html)


@permission_required(VIEW, raise_exception=True)
def announce_page(request):
    from buildings.views import clean_url

    today = timezone.localdate()
    chosen = rules.chosen_a_filter(request.GET.get("f", ""))
    horizon = rules.chosen_horizon(request.GET.get("zeitraum", ""))
    kind = request.GET.get("art", "") if request.GET.get("art", "") in ("reading", "installation") else ""
    query = request.GET.get("q", "").strip()
    rows = notices.announcement_rows(today, None if horizon == "alle" else int(horizon), kind, query,
                                     services.get_notice_selection(request.session))
    shown = [r for r in rows if rules.in_a_filter(chosen, r.state)]
    context = {
        "may_edit": may_edit(request.user), "rows": shown, "counts": rules.count_a_filters(r.state for r in rows), "filters": rules.A_FILTERS,
        "chosen": chosen, "horizons": rules.HORIZONS, "horizon": horizon, "art": kind, "q": query, "today": today,
        "channels": rules.CHANNELS, "scopes": rules.SCOPES, "to_print": [r for r in shown if r.state == rules.A_PRINT],
        "late": sum(1 for r in rows if r.late), **_bar(request.session),
    }
    if request.htmx_target == "announce-list":
        response = render(request, "documents/_announce_list.html", context)
        response["HX-Push-Url"] = clean_url(request)
        return response
    return render(request, "documents/announce.html", context)


def _stop(pk):
    return get_object_or_404(TourStop.objects.select_related("tour__employee", "building__property_manager",
                                                             "installation_order__building"), pk=pk)


@require_POST
@edit_required
def announce_save(request, pk):
    """✎ Ankündigung: how (Aushang / Brief / Mail an HV ...), for whom (Haus / Wohnungen), the time window."""
    stop = _stop(pk)
    for name in ("von", "bis"):
        if request.POST.get(name, "").strip() and rules.parse_time(request.POST.get(name)) is None:
            response = _row_context(request, notices.row_of(stop, timezone.localdate()),
                                    "Zeit bitte als 8:00 oder 08:30 eingeben.", error=True)
            response["HX-Reswap"] = "none"
            return response
    changes = notices.set_announcement(stop, request.user, channel=request.POST.get("channel"),
                                       scope=request.POST.get("scope"), units=request.POST.get("units"),
                                       window_from=request.POST.get("von"), window_to=request.POST.get("bis"))
    response = _row_context(request, notices.row_of(stop, timezone.localdate(), services.get_notice_selection(request.session)),
                            "Gespeichert: " + ", ".join(changes) if changes else "Nichts geändert")
    response["HX-Trigger"] = "notices-changed"
    return response


@require_POST
@edit_required
def announce_sent(request, pk):
    """✓ aufgehängt / Brief verschickt / Mail an HV verschickt … - or ↺ back."""
    stop = _stop(pk)
    done = request.POST.get("done") == "1"
    if done and not stop.notice_channel:
        return _row_context(request, notices.row_of(stop, timezone.localdate(), services.get_notice_selection(request.session)),
                            "Bitte zuerst wählen, wie angekündigt wird (✎).", error=True)
    notices.mark_sent(stop, request.user, done)
    row = notices.row_of(stop, timezone.localdate(), services.get_notice_selection(request.session))
    response = _row_context(request, row, f"✓ {row.sent_label}" if done else "Wieder offen")
    response["HX-Trigger"] = "notices-changed"
    return response


@require_POST
@permission_required("planning.add_tour", raise_exception=True)
def notice_select(request):
    """☐ for the 📄 Aushang-Fahrt (then 🗺 Aushang-Fahrt planen = the normal plan dialog)."""
    def number(name):
        value = request.POST.get(name, "")
        return int(value) if value.isdigit() else None
    services.toggle_notice_selection(request.session, number("building"), number("order"), "checked" in request.POST)
    return render(request, "documents/_notice_plan_bar.html", _bar(request.session))


@require_POST
@permission_required("planning.add_tour", raise_exception=True)
def notice_select_clear(request):
    services.clear_notice_selection(request.session)
    response = HttpResponse("")
    response["HX-Refresh"] = "true"
    return response
