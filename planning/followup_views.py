"""🧾 Rückmeldungen: the office worklist after the visits (planning/followup.py)."""

import datetime
import re

from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST

from buildings.views import clean_url
from journal.activity import day_label, record
from journal.models import ActivityKind

from . import followup, services, visits
from .models import StopKind, TourStop
from .rules.followup import CLOSE_PICKS, FILTERS, MISSING_LOOKBACK_DAYS, STATE_LABELS, chosen_filter, in_filter
from .rules.visits import OUTCOMES, REASON_LABELS, REASONS

VIEW = "planning.view_tour"
KEY = re.compile(r"^[vsn]\d{1,12}$")


def _day(value):
    try:
        return datetime.date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _context(request, params=None):
    params = params if params is not None else request.GET
    today = timezone.localdate()
    day = _day(params.get("tag", ""))
    chosen = chosen_filter(params.get("f", ""), day is not None)
    kind = params.get("art", "") if params.get("art", "") in ("reading", "installation") else ""
    query = params.get("q", "").strip()
    entries = followup.decorate(followup.followup_entries(today), request.user, today)
    filtered = followup.filter_entries(entries, day=day, kind=kind, query=query)
    shown = [e for e in filtered if in_filter(chosen, e.state, e.has_problem)]
    session = request.session
    return {
        "days": followup.group_days(shown, filtered, newest_first=chosen in ("erledigt", "alle") and day is None),
        "counts": followup.counts(filtered), "chosen": chosen, "filters": FILTERS, "labels": STATE_LABELS,
        "day": day, "previous_day": (day or today) - datetime.timedelta(days=1),
        "next_day": (day or today) + datetime.timedelta(days=1), "art": kind, "q": query, "today": today,
        "stamp": followup.stamp(), "close_picks": CLOSE_PICKS, "reasons": REASONS, "reasons_dict": REASON_LABELS,
        "lookback": MISSING_LOOKBACK_DAYS,
        "selection": set(services.get_selection(session)), "order_selection": set(services.get_order_selection(session)),
        "reading_bar": services.plan_bar_context(session), "order_bar": services.order_bar_context(session),
    }


def _entry_context(request):
    session = request.session
    return {"labels": STATE_LABELS, "close_picks": CLOSE_PICKS, "reasons": REASONS, "reasons_dict": REASON_LABELS,
            "today": timezone.localdate(), "selection": set(services.get_selection(session)),
            "order_selection": set(services.get_order_selection(session))}


@login_required
def followup_page(request):
    if not request.user.has_perm(VIEW):
        raise PermissionDenied
    context = _context(request)
    if request.htmx_target == "followup-list":
        response = render(request, "planning/_followup_list.html", {**context, "oob": True})
        response["HX-Push-Url"] = clean_url(request)
        return response
    return render(request, "planning/followup.html", context)


@login_required
def followup_badge(request):
    if not request.user.has_perm(VIEW):
        return HttpResponse("")
    return render(request, "planning/_followup_badge.html", {"count": followup.open_count(timezone.localdate())})


@login_required
def followup_counts(request):
    if not request.user.has_perm(VIEW):
        raise PermissionDenied
    context = _context(request)
    context["news"] = request.GET.get("stand", "") != context["stamp"]
    context["stamp"] = request.GET.get("stand", "") or context["stamp"]  # keep the old stand until the list is reloaded
    return render(request, "planning/_followup_counts.html", context)


def _error(request, message):
    response = render(request, "core/_toast.html", {"message": message, "error": True})
    response["HX-Reswap"] = "none"
    return response


def _answer(request, e, message, events):
    html = render_to_string("planning/_followup_entry.html", {**_entry_context(request), "e": e}, request=request)
    html += render_to_string("core/_toast.html", {"message": message}, request=request)
    response = HttpResponse(html)
    response["HX-Trigger"] = "followup-changed, " + events
    return response


@require_POST
@login_required
def followup_action(request, key):
    if not request.user.has_perm(VIEW):
        raise PermissionDenied
    if not KEY.match(key):
        raise Http404
    today = timezone.localdate()
    e = followup.entry(key, request.user, today)
    if e is None:
        return _error(request, "Diese Rückmeldung gibt es nicht mehr – bitte die Liste neu laden.")
    try:
        message, events = followup.act(e, request.POST.get("action", ""), request.user, request.POST)
    except ValidationError as error:
        return _error(request, error.messages[0])
    except PermissionDenied as error:
        return _error(request, str(error) or "Das darf deine Rolle nicht.")
    fresh = followup.entry(key, request.user, today) or e
    return _answer(request, fresh, message, events)


@require_POST
@permission_required("planning.process_visit", raise_exception=True)
def followup_report(request, pk):
    """❓ keine Rückmeldung: the office enters the Ergebnis after a phone call."""
    stop = get_object_or_404(TourStop.objects.select_related("tour__employee", "building", "installation_order"), pk=pk)
    outcome, todo, reason = request.POST.get("outcome", ""), request.POST.get("todo", ""), request.POST.get("reason", "")
    try:
        visit = visits.report(stop, request.user, outcome, todo, reason, on_behalf=True)
    except (ValidationError, PermissionDenied) as error:
        return _error(request, error.messages[0] if hasattr(error, "messages") else str(error))
    target = stop.building if stop.kind == StopKind.READING else stop.installation_order
    why = f" – {REASON_LABELS[reason]}" if outcome == "absent" and reason in REASON_LABELS else ""
    rest = f" · noch zu tun: {todo.strip()}" if todo.strip() and outcome != "complete" else ""
    record(request.user, ActivityKind.OFFICE, f"📝 im Büro nachgetragen: {OUTCOMES[outcome]}{why}: {target.street} "
           f"({stop.tour.employee} {day_label(stop.tour.date)}){rest}", tour=stop.tour, building=stop.building,
           order=stop.installation_order)
    e = followup.entry(f"v{visit.pk}", request.user, timezone.localdate())
    response = _answer(request, e, "Ergebnis nachgetragen", "buildings-changed, orders-changed")
    return response


@require_POST
@permission_required("planning.process_visit", raise_exception=True)
def followup_quiet(request):
    """'✓ n unauffällige abhaken' for one day."""
    day = _day(request.POST.get("tag", ""))
    if day is None:
        return _error(request, "Kein Tag gewählt.")
    done = followup.check_quiet(day, request.user, timezone.localdate())
    context = _context(request, request.POST)
    days = [d for d in context["days"] if d["date"] == day]
    html = render_to_string("planning/_followup_day.html", {**context, "d": days[0]} if days else context, request=request) \
        if days else f'<section class="fu-day" id="fu-day-{day:%Y-%m-%d}"></section>'
    html += render_to_string("core/_toast.html", {"message": f"{done} unauffällige abgehakt"}, request=request)
    response = HttpResponse(html)
    response["HX-Trigger"] = "followup-changed"
    return response
