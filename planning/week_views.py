"""🗓 Wochenplanung by hand (planning/week.py): board, put on / take off, check, save the week."""

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services, week
from .models import Employee
from .rules import week as rules

PLAN = "planning.add_tour"


def _params(data):
    today = timezone.localdate()
    monday = rules.chosen_monday(data.get("kw", ""), today)
    kind = data.get("art", "") if data.get("art", "") in ("reading", "installation") else ""
    return monday, kind, data.get("q", "").strip()


def _context(request, monday, kind, query):
    session = request.session
    days = week.get_days(session, monday)
    items, found, windows = week.pool(kind, query, days, set(services.get_selection(session)),
                                      set(services.get_order_selection(session)))
    return {
        "monday": monday, "label": rules.week_label(monday), "dates": rules.week_days(monday), "art": kind, "q": query,
        "previous": monday - datetime.timedelta(days=7), "next": monday + datetime.timedelta(days=7),
        "this_week": rules.monday_of(timezone.localdate()),
        "rows": week.board(monday, kind, days, windows), "pool": items, "pool_found": found,
        "pool_limit": week.POOL_LIMIT, "totals": rules.totals(days),
        "totals_hours": rules.hours(rules.totals(days)["minutes"]),
    }


@permission_required(PLAN, raise_exception=True)
def week_page(request):
    monday, kind, query = _params(request.GET)
    context = _context(request, monday, kind, query)
    if request.htmx_target == "week-pool":
        return render(request, "planning/_week_pool.html", context)
    return render(request, "planning/week.html", context)


def _answer(request, monday, kind, query, message="", error=False):
    """The board again + the list (out of band) + a short note."""
    context = _context(request, monday, kind, query)
    html = render_to_string("planning/_week_board.html", context, request=request)
    html += render_to_string("planning/_week_pool.html", {**context, "oob": True}, request=request)
    if message:
        html += render_to_string("core/_toast.html", {"message": message, "error": error}, request=request)
    return HttpResponse(html)


def _cell(request):
    try:
        employee = Employee.objects.get(pk=int(request.POST.get("employee", "")), active=True)
        date = datetime.date.fromisoformat(request.POST.get("date", ""))
    except (Employee.DoesNotExist, ValueError):
        return None, None
    return employee, date


@require_POST
@permission_required(PLAN, raise_exception=True)
def week_place(request):
    """Put the ticked (or dragged) objects on one person and day - the office decides."""
    monday, kind, query = _params(request.POST)
    employee, date = _cell(request)
    if employee is None:
        return _answer(request, monday, kind, query, "Diesen Tag gibt es nicht.", error=True)
    problem = week.cell_problem(employee, date, monday)
    if problem:
        return _answer(request, monday, kind, query, problem, error=True)
    items = [i for i in (rules.parse_item(v) for v in request.POST.getlist("item")) if i]
    if not items:
        return _answer(request, monday, kind, query, "Erst links Objekte ankreuzen (oder eins hierher ziehen).", error=True)
    wrong = [i for i in items if not rules.can_take(i["kind"], employee.can_read, employee.can_install)]
    items = [i for i in items if i not in wrong]
    if not items:
        what = "ablesen" if wrong[0]["kind"] == "reading" else "montieren"
        return _answer(request, monday, kind, query, f"{employee} darf laut Stammdaten nicht {what}.", error=True)
    days, moved = rules.place(week.get_days(request.session, monday), employee.pk, date.isoformat(), items)
    week.recount(days, moved | {(employee.pk, date.isoformat())})
    week.set_days(request.session, monday, days)
    day = rules.find_day(days, employee.pk, date.isoformat())
    total, level = rules.day_load(day["work"], day["drive"], employee.max_daily_minutes)
    message = f"{len(items)} → {employee} {rules.WEEKDAYS[date.weekday()]} {date:%d.%m.} · ≈ {rules.hours(total)}"
    if level == "over":
        message += f" – mehr als {rules.hours(employee.max_daily_minutes)}!"
    if wrong:
        message += f" · {len(wrong)} nicht übernommen (Art passt nicht zur Person)"
    return _answer(request, monday, kind, query, message, error=level == "over")


@require_POST
@permission_required(PLAN, raise_exception=True)
def week_remove(request):
    """✕ one object off a day, or the whole day (no item)."""
    monday, kind, query = _params(request.POST)
    employee, date = _cell(request)
    if employee is None:
        return _answer(request, monday, kind, query)
    item = rules.parse_item(request.POST.get("item", "")) if request.POST.get("item") else None
    days = rules.remove(week.get_days(request.session, monday), employee.pk, date.isoformat(), item)
    week.recount(days, {(employee.pk, date.isoformat())})
    week.set_days(request.session, monday, days)
    return _answer(request, monday, kind, query, "Zurück in „Noch nicht geplant“" if item else "Tag geleert")


@require_POST
@permission_required(PLAN, raise_exception=True)
def week_open(request):
    """🔍 one day into "Fahrplan prüfen" (exact TomTom times, order, Bist du sicher?)."""
    monday, kind, query = _params(request.POST)
    employee, date = _cell(request)
    days = week.get_days(request.session, monday)
    day = rules.find_day(days, employee.pk, date.isoformat()) if employee else None
    if day is None:
        return _answer(request, monday, kind, query, "Diesen Tag gibt es im Entwurf nicht mehr.", error=True)
    request.session[services.DRAFT_KEY] = services.autoplan_draft(day)
    week.set_days(request.session, monday, rules.remove(days, employee.pk, date.isoformat()))
    response = HttpResponse("")
    response["HX-Redirect"] = reverse("planning:draft")
    return response


@require_POST
@permission_required(PLAN, raise_exception=True)
def week_save(request):
    """Alle vorläufig erstellen: every day of this week's draft becomes a PROVISIONAL plan."""
    monday, kind, query = _params(request.POST)
    days = week.get_days(request.session, monday)
    if not days:
        return _answer(request, monday, kind, query, "Im Entwurf dieser Woche ist nichts.", error=True)
    saved, problems, kept = week.save_week(days, monday, request.user)
    week.set_days(request.session, monday, kept)
    if saved:
        messages.success(request, f"{len(saved)} Fahrpläne vorläufig erstellt – bitte im Kalender prüfen und bestätigen.")
    for problem in problems:
        messages.error(request, problem)
    response = HttpResponse("")
    response["HX-Refresh"] = "true"
    return response


@require_POST
@permission_required(PLAN, raise_exception=True)
def week_clear(request):
    monday, kind, query = _params(request.POST)
    week.set_days(request.session, monday, [])
    return _answer(request, monday, kind, query, "Entwurf der Woche verworfen")
