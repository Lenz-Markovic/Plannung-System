"""
Tour planning pages (see planning/services.py for the workflow).

All pages work with HTMX:
- the checkbox in a list row posts to select(); the answer is the updated
  "Fahrplan erstellen (n)" button in the page header
- the button opens the dialog (plan_dialog) in #modal
- the preview page reloads only #preview after each change
"""

import datetime
from urllib.parse import quote_plus

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Max, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST

from buildings.models import Building, BuildingStatus, InstallationOrder
from buildings.services import propose_status
from core.models import Features
from documents import notices
from documents.notice_rules import notice_deadline
from journal import notes
from journal.activity import day_label, record, streets
from journal.models import ActivityKind

from documents import notice_rules

from . import dayplan, services, visits
from .calendar import calendar_events, free_day_events, tour_kind
from .display import preview_map_data, route_sketch, tour_map_data
from .excel import build_workbook
from .forms import DraftSettingsForm, PlanForm
from .models import Absence, Employee, StopKind, Tour, TourStop, Visit
from .rules.ordering import STRATEGIES
from .rules.followup import CLOSE_PICKS
from .rules.visits import REASONS
from .tomtom import TomTomError, current_api_key, get_client

PLAN_PERMISSION = "planning.add_tour"


def _plan_bar(request):
    return render(request, "planning/_plan_bar.html", services.plan_bar_context(request.session))


@require_POST
@permission_required(PLAN_PERMISSION, raise_exception=True)
def select(request):
    """Checkbox in a row: add or remove the building from the selection."""
    services.toggle_selection(request.session, int(request.POST["building"]), "checked" in request.POST)
    return _plan_bar(request)


@require_POST
@permission_required(PLAN_PERMISSION, raise_exception=True)
def select_clear(request):
    services.clear_selection(request.session)
    response = HttpResponse("")
    response["HX-Refresh"] = "true"  # reload the page so all checkboxes are empty again
    return response


@permission_required(PLAN_PERMISSION, raise_exception=True)
def plan_dialog(request):
    """"Fahrplan erstellen" / "Montage planen": everything ticked in BOTH lists.

    Buildings (🏢 Liegenschaften) become readings, orders (🔧 Montage)
    installations - so one plan can hold both.
    """
    buildings = list(Building.objects.filter(pk__in=services.get_selection(request.session)).order_by("file_number"))
    orders = list(InstallationOrder.objects.filter(pk__in=services.get_order_selection(request.session)).order_by("re_number"))
    initial = {key: value for key, value in (("date", request.GET.get("datum")), ("employee", request.GET.get("person"))) if value}
    form = PlanForm(request.POST or None, initial=initial, readings=bool(buildings) or not orders, installations=bool(orders))
    if request.method == "POST" and form.is_valid() and (buildings or orders):
        data = form.cleaned_data
        request.session[services.DRAFT_KEY] = services.create_draft(
            [b.pk for b in buildings], data["employee"], data["date"], data["start"], data["break_minutes"], data["strategy"],
            order_ids=[o.pk for o in orders])
        response = HttpResponse("")
        response["HX-Redirect"] = reverse("planning:draft")
        return response
    return render(request, "planning/_plan_dialog.html", {
        "form": form, "buildings": buildings, "orders": orders,
        "reading_minutes": sum(b.reading_minutes for b in buildings),
        "installation_minutes": sum(o.duration_minutes for o in orders),
    })


def _draft_or_none(request):
    return request.session.get(services.DRAFT_KEY)


def _team_candidates(draft):
    """People who can join the team: free that day (no own plan, not in another team, not absent)."""
    date = datetime.date.fromisoformat(draft["date"])
    busy = Tour.objects.filter(date=date).exclude(pk=draft.get("tour_id"))
    absent = Absence.objects.filter(start_date__lte=date, end_date__gte=date).values("employee")
    return (Employee.objects.filter(active=True).exclude(pk__in=[draft["employee"], *draft.get("team", [])])
            .exclude(pk__in=busy.values("employee"))
            # only real team members: "NOT IN" with an empty (NULL) value would exclude everybody
            .exclude(pk__in=busy.filter(team__isnull=False).values("team")).exclude(pk__in=absent))


def _render_preview(request, draft, template="planning/_preview.html"):
    preview = services.calculate_preview(draft)
    notes.attach_notes(preview.stops)  # 📝 notes of the Terminierung at every stop (⛔ Storno in red)
    visits.attach_attempts(preview.stops, preview.date)  # 🔁 2. Termin? what is still to do from last time
    settings_form = DraftSettingsForm(initial={"start": draft["start"], "break_minutes": draft["break"]})
    suggestions, too_long = services.draft_suggestions(draft, net_minutes=preview.day_plan.net_minutes)
    response = render(request, template, {"preview": preview, "draft": draft, "settings_form": settings_form,
                                          "suggestions": suggestions, "suggestions_too_long": too_long,
                                          "team_candidates": _team_candidates(draft),
                                          "strategies": STRATEGIES, "sketch": route_sketch(preview.stops),
                                          "map_data": preview_map_data(preview.stops), "map_available": preview.has_tomtom,
                                          "notice_choices": notice_rules.CHOICES, "access_scopes": notice_rules.ACCESS_SCOPES})
    response.preview = preview  # for draft_action (notification about the working time)
    return response


@permission_required(PLAN_PERMISSION, raise_exception=True)
def draft(request):
    current = _draft_or_none(request)
    if not current:
        messages.info(request, "Kein Fahrplan in Bearbeitung – bitte Liegenschaften auswählen.")
        return redirect("buildings:list")
    return _render_preview(request, current, "planning/draft.html")


@require_POST
@permission_required(PLAN_PERMISSION, raise_exception=True)
def draft_action(request):
    """▲ ▼ ✕, new sorting or new start/break -> recalculate the preview."""
    current = _draft_or_none(request)
    if not current:
        return HttpResponse(status=204)
    action, index = request.POST.get("action"), int(request.POST.get("index", -1))
    if action == "up":
        services.move_stop(current, index, -1)
    elif action == "down":
        services.move_stop(current, index, +1)
    elif action == "remove":
        services.remove_stop(current, index)
    elif action == "sort" and request.POST.get("strategy") in STRATEGIES:
        current["strategy"] = request.POST["strategy"]
        services.sort_draft(current)
    elif action == "settings":
        form = DraftSettingsForm(request.POST)
        if form.is_valid():
            current["start"] = form.cleaned_data["start"].strftime("%H:%M")
            current["break"] = form.cleaned_data["break_minutes"]
    message, error, info = "", False, False
    if action == "swap":
        # ⇄ in the working-time question: another stop instead of this one
        try:
            message = services.swap_stop(current, index, request.POST.get("kind"), int(request.POST.get("pk", 0)))
        except ValueError as problem:
            message, error = str(problem), True
    elif action in ("team_add", "team_remove", "team_split"):
        # 👥 Team for big objects (planning/services.py: set_team)
        try:
            message = services.set_team(
                current, add=int(request.POST.get("pk") or 0) if action == "team_add" else None,
                remove=int(request.POST.get("pk") or 0) if action == "team_remove" else None,
                split=(request.POST.get("split") == "1") if action == "team_split" else None)
        except ValueError as problem:
            message, error = str(problem), True
    if action in ("notice", "access"):
        # 📄 Ankündigung / 🚪 Zugang of one reading - saved with the plan
        try:
            message = services.set_draft_notice(
                current, index, choice=request.POST.get("choice") if action == "notice" else None,
                access=request.POST.get("access") if action == "access" else None, units=request.POST.get("units", ""))
        except ValueError as problem:
            message, error = str(problem), True
    if action == "add":
        # "+ Stopp hinzufügen": a reading or an installation joins the same plan
        try:
            message = services.add_stop(current, request.POST.get("kind"), int(request.POST.get("pk", 0)))
        except ValueError as problem:
            message, error = str(problem), True
    request.session[services.DRAFT_KEY] = current
    response = _render_preview(request, current)
    if not message and response.preview.time_notice and action != "refresh":
        # information after each change while the day is longer than 7,5 h / shorter than 6 h
        message, info = f"ℹ ⏱ {response.preview.time_notice.text}", True
    if message:
        response.content += render(request, "core/_toast.html", {"message": message, "error": error, "info": info}).content
    if request.POST.get("close_modal"):
        response.content += b'<div id="modal" hx-swap-oob="true"></div>'  # the question dialog closes
    return response


@permission_required(PLAN_PERMISSION, raise_exception=True)
def plan_confirm(request):
    """Last step: "Bist du sicher?" with a summary - and, for a day over 7,5 h / under 6 h,
    options that make sense (swap with a stop nearby, take out, add). Only from here a plan is saved."""
    current = _draft_or_none(request)
    if not current:
        return HttpResponse(status=204)
    preview = services.calculate_preview(current)
    notes.attach_notes(preview.stops)
    visits.attach_attempts(preview.stops, preview.date)
    return render(request, "planning/_plan_confirm.html", {
        "preview": preview, "options": services.time_options(current, preview),
    })


@permission_required(PLAN_PERMISSION, raise_exception=True)
def confirm_search(request):
    """Search field in "Bist du sicher?": type an RE number, AZ or address yourself."""
    current = _draft_or_none(request)
    query = request.GET.get("q", "").strip()
    if not current or len(query) < 2:
        return HttpResponse("")
    preview = services.calculate_preview(current)
    results = services.rate_search_results(services.search_targets(query, current), current, preview)
    return render(request, "planning/_confirm_search.html", {"results": results, "query": query,
                                                             "too_long": bool(preview.time_notice and preview.time_notice.kind == "over")})


@permission_required(PLAN_PERMISSION, raise_exception=True)
def draft_search(request):
    """Search box in the preview: buildings and orders to add to the plan."""
    current = _draft_or_none(request)
    results = services.search_targets(request.GET.get("q", ""), current) if current else []
    return render(request, "planning/_draft_search.html", {"results": results, "query": request.GET.get("q", "")})


@require_POST
@permission_required(PLAN_PERMISSION, raise_exception=True)
def draft_save(request):
    current = _draft_or_none(request)
    if not current:
        return redirect("buildings:list")
    confirm = request.POST.get("confirm") == "1"
    if confirm and not request.user.has_perm("planning.confirm_tour"):
        messages.error(request, "Deine Rolle darf Fahrpläne nicht bestätigen.")
        return redirect("planning:draft")
    # Only after "Bist du sicher?" was answered (planning/_plan_confirm.html)
    if request.POST.get("sure") != "1":
        messages.warning(request, "Bitte unten auf „Fahrplan erstellen …“ klicken und die Frage beantworten.")
        return redirect("planning:draft")
    try:
        tour = services.save_draft(current, request.user, confirm=confirm)
    except services.ConcurrentChange as error:
        messages.error(request, f"{error} Bitte die Planung neu starten.")
        return redirect("planning:draft")
    except ValueError as error:
        messages.error(request, str(error))
        return redirect("planning:draft")
    del request.session[services.DRAFT_KEY]
    services.clear_selection(request.session)
    services.clear_order_selection(request.session)
    state = "bestätigt" if confirm else "vorläufig gespeichert"
    count = tour.stops.count()
    messages.success(request, f"Fahrplan {tour.employee} am {tour.date:%d.%m.%Y} {state} ({count} Stopp{'s' if count != 1 else ''}).")
    # like the prototype: jump to the calendar on that day
    return redirect(f"{reverse('planning:calendar')}?datum={tour.date.isoformat()}&excel={tour.pk}")


@require_POST
@permission_required(PLAN_PERMISSION, raise_exception=True)
def draft_discard(request):
    request.session.pop(services.DRAFT_KEY, None)
    messages.info(request, "Planung verworfen – die Auswahl bleibt erhalten.")
    return redirect("buildings:list")


# =============================================================================
# Calendar
# =============================================================================


def _calendar_employees(user):
    """Office roles see everybody; readers only themselves ("nur eigene Termine")."""
    if user.has_perm("planning.view_tour"):
        return Employee.objects.filter(active=True)
    return Employee.objects.filter(user=user)


def _may_see_calendar(user):
    return user.has_perm("planning.view_tour") or user.has_perm("planning.view_own_tours")


@login_required
def calendar_page(request):
    if not _may_see_calendar(request.user):
        return render(request, "403.html", status=403)
    employees = _calendar_employees(request.user)
    return render(request, "planning/calendar.html", {
        "employees": employees,
        "editable": request.user.has_perm("planning.change_tour"),
        "initial_date": request.GET.get("datum", ""),
        # after saving a plan: its Excel file is downloaded automatically
        "excel_tour": Tour.objects.filter(pk=request.GET.get("excel") or 0).first(),
    })


@login_required
def calendar_feed(request):
    """JSON for FullCalendar: ?start=...&end=...&person=<id>."""
    if not _may_see_calendar(request.user):
        return JsonResponse([], safe=False, status=403)
    start = datetime.date.fromisoformat(request.GET["start"][:10])
    end = datetime.date.fromisoformat(request.GET["end"][:10])
    employees = _calendar_employees(request.user)
    if request.GET.get("person"):
        employees = employees.filter(pk=request.GET["person"])
    editable = request.user.has_perm("planning.change_tour")
    kind = request.GET.get("art") if request.GET.get("art") in ("reading", "installation", "mixed") else ""
    events = calendar_events(start, end, employees, editable, kind)
    if request.GET.get("frei") == "1" and request.user.has_perm("planning.view_tour"):
        # 🗓 button: first free day of everybody
        events += free_day_events(start, end, employees, kind, can_plan=request.user.has_perm(PLAN_PERMISSION))
    return JsonResponse(events, safe=False)


@login_required
def day_overview(request):
    """Side panel after a click on a day: who is planned, who is absent, who is still free."""
    if not request.user.has_perm("planning.view_tour"):
        return HttpResponse(status=403)
    try:
        date = datetime.date.fromisoformat(request.GET.get("datum", ""))
    except ValueError:
        return HttpResponse(status=400)
    tours = list(Tour.objects.filter(date=date).select_related("employee").prefetch_related("stops", "team"))
    for tour in tours:
        tour.kind = tour_kind(list(tour.stops.all()))
    absent = {a.employee_id: a for a in Absence.objects.filter(start_date__lte=date, end_date__gte=date).select_related("employee")}
    planned = {person.pk for t in tours for person in t.people}
    free = [e for e in Employee.objects.filter(active=True) if e.pk not in planned and e.pk not in absent]
    selection = services.plan_bar_context(request.session)
    # "planen" only for people who can do what is ticked (readings -> reader, orders -> installer)
    for employee in free:
        employee.fits = ((not selection["selected_count"] or employee.can_read)
                         and (not selection["other_count"] or employee.can_install))
    return render(request, "planning/_day_panel.html", {
        "date": date, "tours": tours, "absent": list(absent.values()), "free": free,
        "selected": selection["selected_count"] + selection["other_count"], "weekend": date.weekday() >= 5,
    })


@permission_required(PLAN_PERMISSION, raise_exception=True)
def free_plan(request):
    """Click on a green "🟢 frei" marker: plan this person's free day with suggestions."""
    employee = get_object_or_404(Employee, pk=request.GET.get("person") or request.POST.get("person") or 0, active=True)
    try:
        date = datetime.date.fromisoformat(request.GET.get("datum") or request.POST.get("datum") or "")
    except ValueError:
        return HttpResponse(status=400)
    kind = request.GET.get("art", "")
    if request.method == "POST":
        building_ids = [int(pk) for pk in request.POST.getlist("building")]
        order_ids = [int(pk) for pk in request.POST.getlist("order")]
        if request.POST.get("with_selection"):  # also what is ticked in the lists
            building_ids += services.get_selection(request.session)
            order_ids += services.get_order_selection(request.session)
        if not building_ids and not order_ids:
            return render(request, "core/_toast.html", {"message": "Bitte mindestens einen Stopp ankreuzen.", "error": True})
        request.session[services.DRAFT_KEY] = services.create_draft(
            list(dict.fromkeys(building_ids)), employee, date, employee.default_start_time, 30, "far",
            order_ids=list(dict.fromkeys(order_ids)))
        response = HttpResponse("")
        response["HX-Redirect"] = reverse("planning:draft")
        return response
    suggestions, ticked = services.free_day_suggestions(employee, date, kind)
    for suggestion in suggestions:
        suggestion.ticked = (suggestion.kind, suggestion.pk) in ticked
    selection = services.plan_bar_context(request.session)
    return render(request, "planning/_free_plan.html", {
        "employee": employee, "date": date, "suggestions": suggestions,
        "selected_buildings": selection["selected_count"], "selected_orders": selection["other_count"],
        "ticked_minutes": sum(s.minutes for s in suggestions if (s.kind, s.pk) in ticked),
    })


@login_required
def free_days(request):
    """Side panel "🗓 Erster freier Tag": for every reader / installer the first day without a plan."""
    if not request.user.has_perm("planning.view_tour"):
        return HttpResponse(status=403)
    today = timezone.localdate()
    people = Employee.objects.filter(active=True)
    kind = request.GET.get("art", "")
    if kind == "reading":
        people = people.filter(can_read=True)
    elif kind == "installation":
        people = people.filter(can_install=True)
    days = services.first_free_days(people, today)
    rows = sorted(days.items(), key=lambda item: (item[1] or datetime.date.max, item[0].short_name))
    return render(request, "planning/_free_days.html", {"rows": rows, "today": today, "kind": kind})


@login_required
def person_overview(request, pk):
    """Side panel after a click on a person in the calendar: their plans at a glance."""
    employee = get_object_or_404(_calendar_employees(request.user), pk=pk)
    today = timezone.localdate()
    monday = today - datetime.timedelta(days=today.weekday())
    tours = list(Tour.objects.filter(services.tours_with(employee), date__gte=today).distinct().order_by("date")
                 .prefetch_related("stops")[:12])
    for tour in tours:
        tour.kind = tour_kind(list(tour.stops.all()))
        # imported plans have no end time yet: start + work + drive, like the calendar
        tour.end_shown = tour.end_time or (datetime.datetime.combine(tour.date, tour.start_time)
                                           + datetime.timedelta(minutes=tour.net_minutes)).time()
    weeks = []
    # Weekly hours: only for roles with "Wochenstunden sehen" (Admin by default)
    for offset in range(3 if request.user.has_perm("planning.view_week_hours") else 0):
        start = monday + datetime.timedelta(weeks=offset)
        week_tours = Tour.objects.filter(services.tours_with(employee), date__gte=start,
                                         date__lt=start + datetime.timedelta(days=7)).distinct()
        minutes = sum(t.work_minutes + t.drive_minutes for t in week_tours)
        weeks.append({"start": start, "minutes": minutes, "days": week_tours.count(),
                      "percent": min(100, round(minutes / (5 * 450) * 100))})  # 5 days × 7.5 h
    open_orders = (InstallationOrder.objects.filter(assigned_installers=employee, tour_stops__isnull=True)
                   .exclude(status="done").order_by("re_number"))
    return render(request, "planning/_person.html", {
        "employee": employee, "tours": tours, "weeks": weeks, "open_orders": open_orders,
        "first_free": services.first_free_days([employee], today)[employee],
        "absences": Absence.objects.filter(employee=employee, end_date__gte=today).order_by("start_date")[:5],
    })


@login_required
def tour_detail(request, pk):
    """Side panel with the stops of one tour (opened by clicking an event)."""
    tour = get_object_or_404(Tour.objects.select_related("employee"), pk=pk)
    if not (request.user.has_perm("planning.view_tour") or tour.employee.user_id == request.user.pk):
        return HttpResponse(status=403)
    stamp = _panel_stamp(tour)
    if request.GET.get("check") == stamp:
        return HttpResponse(status=204)  # polling: nothing new, the panel stays as it is
    stops = list(TourStop.objects.filter(tour=tour).select_related("building", "installation_order", "help_tour__employee",
                                                                   "notice_printed_by", "done_by",
                                                                   "building__proposed_status_by").order_by("position"))
    office = request.user.has_perm("planning.view_tour")  # Ableser/Monteur: no Aushang, no Verlauf on their side
    helps = list(TourStop.objects.filter(kind=StopKind.HELP, help_tour=tour).select_related("tour__employee"))
    estimates = notices.estimated_times(tour) if office else {}  # a plan without times: the notice gets estimated ones
    for stop in stops:
        stop.helpers = [h for h in helps if h.kind == StopKind.HELP and services.same_object(
            h, stop.building_id, stop.installation_order_id)] if stop.kind != StopKind.HELP else []
        # tenant notice (Aushang), optional per stop: missing / late / printed / outdated
        stop.notice_possible = office and stop.kind != StopKind.HELP
        stop.notice = notices.state_of(stop, estimates=estimates) if office and notices.wanted(stop) else None
    notes.attach_notes(stops)  # 📝 open notes of each building / order
    if not request.user.has_perm("journal.view_note"):
        for stop in stops:  # Ableser/Monteur: only the problems they reported themselves, not the office notes
            stop.notes = [n for n in stop.notes if n.kind == "problem"]
            stop.storno = False
    visits.attach_attempts(stops, tour.date)  # 🔁 which visit is this, and the Ergebnis
    for stop in stops:  # a note on site that went to the office as ⚠ problem is shown once, as the problem
        stop.field_is_problem = any(n.kind == "problem" and n.text == stop.field_note for n in stop.notes)
    notice_states = [s.notice.state for s in stops if s.notice]
    # who can help at one object: everybody active who is not in this plan and not fixed in a team that day
    in_teams = Tour.objects.filter(date=tour.date, team__isnull=False).values("team")  # no NULLs in "NOT IN"
    helper_candidates = (Employee.objects.filter(active=True).exclude(pk__in=[p.pk for p in tour.people])
                         .exclude(pk__in=in_teams).exclude(pk__in=Absence.objects.filter(
                             start_date__lte=tour.date, end_date__gte=tour.date).values("employee")))
    return render(request, "planning/_tour_detail.html", {
        "tour": tour, "stops": stops, "helper_candidates": helper_candidates, "stamp": stamp,
        "visited_count": sum(1 for s in stops if services.is_visited(s)),
        # the last change (who to ask) - the full Verlauf is behind the user name in the navigation
        "last_change": tour.activities.select_related("user").first() if request.user.has_perm("journal.view_activity") else None,
        "notice_stops": [s for s in stops if s.notice], "notice_deadline": notice_deadline(tour.date),
        "notice_possible": [s for s in stops if s.notice_possible],
        "notice_off": [s for s in stops if s.notice_possible and not s.notice],
        "notices_open": sum(1 for state in notice_states if state != "printed"),
        "notices_late": "late" in notice_states,
        "notices_too_late": timezone.localdate() > notice_deadline(tour.date),  # printing asks "trotzdem?"
        "notices_outdated": "outdated" in notice_states, "map_data": tour_map_data(stops), "map_available": bool(current_api_key()),
        # moving to another person: readers for readings, installers for installations, both for mixed plans
        "employees": _move_candidates(stops),
    })


def _panel_stamp(tour):
    """Changes when anything in the side panel changes (stops, reports from Mein Tag, notes, Verlauf)."""
    from journal.models import Activity, Note

    stops = tour.stops.aggregate(last=Max("updated_at"))["last"]
    ids = list(tour.stops.values_list("building_id", "installation_order_id"))
    buildings, orders = {b for b, _ in ids if b}, {o for _, o in ids if o}
    notes = Note.objects.filter(Q(building__in=buildings) | Q(installation_order__in=orders))
    last_note = notes.aggregate(n=Max("pk"), r=Max("resolved_at"))
    last_activity = Activity.objects.filter(tour=tour).aggregate(n=Max("pk"))["n"]
    proposals = "".join(Building.objects.filter(pk__in=buildings).order_by("pk").values_list("proposed_status", flat=True))
    return f"{tour.version}-{stops}-{last_note['n']}-{last_note['r']}-{last_activity}-{proposals}"


def _move_candidates(stops):
    kinds = {s.kind for s in stops}
    people = Employee.objects.filter(active=True)
    if StopKind.READING in kinds:
        people = people.filter(can_read=True)
    if StopKind.INSTALLATION in kinds:
        people = people.filter(can_install=True)
    return people if people.exists() else Employee.objects.filter(active=True)  # nobody can do both: show all


@require_POST
@permission_required(PLAN_PERMISSION, raise_exception=True)
def help_request(request, pk):
    """🤝 "Helfer" at one stop in the side panel: opens the helper's day with a help stop."""
    stop = get_object_or_404(TourStop.objects.select_related("tour__employee"), pk=pk)
    helper = get_object_or_404(Employee, pk=request.POST.get("helper") or 0, active=True)
    try:
        request.session[services.DRAFT_KEY] = services.help_draft(stop, helper)
    except ValueError as problem:
        return render(request, "core/_toast.html", {"message": str(problem), "error": True})
    response = HttpResponse("")
    response["HX-Redirect"] = reverse("planning:draft")
    return response


@require_POST
@permission_required("planning.change_tour", raise_exception=True)
def tour_move(request, pk):
    """Drag & drop in the calendar, or "Verschieben" in the side panel.

    Nothing is saved here: the tour becomes a draft for the new day, is
    recalculated (TomTom with the new day's traffic) and shown in the preview.
    Only "übernehmen" there changes the tour.
    """
    tour = get_object_or_404(Tour, pk=pk)

    def answer(message=None, status=200):
        # The calendar (fetch) gets JSON; the form in the side panel (HTMX) a redirect or a message.
        if request.htmx:
            if message:
                return render(request, "core/_toast.html", {"message": message, "error": True}, status=200)
            response = HttpResponse("")
            response["HX-Redirect"] = reverse("planning:draft")
            return response
        return JsonResponse({"message": message} if message else {"redirect": reverse("planning:draft")}, status=status)

    if str(tour.version) != request.POST.get("version", str(tour.version)):
        return answer("Der Fahrplan wurde inzwischen geändert – bitte den Kalender neu laden.", 409)
    new_date = datetime.date.fromisoformat(request.POST.get("date") or tour.date.isoformat())
    employee = get_object_or_404(Employee, pk=request.POST["employee"]) if request.POST.get("employee") else tour.employee
    try:
        request.session[services.DRAFT_KEY] = services.draft_from_tour(tour, new_date, employee)
    except ValueError as error:
        return answer(str(error), 400)
    return answer()


@require_POST
@permission_required("planning.change_tour", raise_exception=True)
def tour_recalculate(request, pk):
    """'Neu rechnen / bearbeiten': open the tour as a draft on the same day."""
    tour = get_object_or_404(Tour, pk=pk)
    request.session[services.DRAFT_KEY] = services.draft_from_tour(tour)
    response = HttpResponse("")
    response["HX-Redirect"] = reverse("planning:draft")
    return response


@require_POST
@permission_required("planning.delete_tour", raise_exception=True)
def tour_delete(request, pk):
    tour = get_object_or_404(Tour, pk=pk)
    label = f"{tour.employee} am {tour.date:%d.%m.%Y}"
    stops = list(tour.stops.select_related("building", "installation_order"))
    open_stops = [s for s in stops if not services.is_visited(s)]
    people, day = tour.people_label, day_label(tour.date)  # before the plan is gone
    if not open_stops and stops:
        return render(request, "core/_toast.html", {
            "message": "Alle Stopps sind schon gemeldet – der Plan bleibt als Nachweis und kann nicht gelöscht werden.",
            "error": True})
    whole = services.delete_tour(tour)
    if whole:
        record(request.user, ActivityKind.PLAN, f"Fahrplan gelöscht: {people} {day} · {len(stops)} Stopps: {streets(stops)}")
        message = f"Fahrplan {label} gelöscht"
    else:
        record(request.user, ActivityKind.PLAN, f"{len(open_stops)} offene Stopps gelöscht: {people} {day} · "
               f"{streets(open_stops)} – die gemeldeten Stopps bleiben als Nachweis", tour=tour)
        message = f"{len(open_stops)} offene Stopps gelöscht – die gemeldeten bleiben als Nachweis im Plan {label}"
    response = render(request, "core/_toast.html", {"message": message})
    response["HX-Trigger"] = "calendar-changed"  # the calendar reloads its events
    return response


# =============================================================================
# Excel export (same layout as the prototype)
# =============================================================================

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _excel_response(tours):
    workbook, filename = build_workbook(tours)
    response = HttpResponse(content_type=XLSX)
    response["Content-Disposition"] = content_disposition_header(as_attachment=True, filename=filename)
    workbook.save(response)
    return response


@login_required
def tour_excel(request, pk):
    """One tour as Excel (downloaded automatically after saving a plan)."""
    tour = get_object_or_404(Tour.objects.select_related("employee"), pk=pk)
    if not (request.user.has_perm("planning.view_tour") or tour.employee.user_id == request.user.pk):
        return HttpResponse(status=403)
    return _excel_response([tour])


@login_required
def tours_excel(request):
    """ "Alle Fahrpläne (Excel)": all tours, optionally of one person and/or from a date on."""
    tours = Tour.objects.filter(employee__in=_calendar_employees(request.user)).select_related("employee")
    if request.GET.get("person"):
        tours = tours.filter(employee_id=request.GET["person"])
    if request.GET.get("ab"):
        tours = tours.filter(date__gte=request.GET["ab"])
    if request.GET.get("bis"):
        tours = tours.filter(date__lte=request.GET["bis"])
    if not tours.exists():
        messages.info(request, "Keine Fahrpläne für diese Auswahl.")
        return redirect("planning:calendar")
    return _excel_response(list(tours))


# =============================================================================
# "Mein Tag": mobile day plan for readers / installers
# =============================================================================


def _day_employee(request):
    """The person whose day is shown: yourself, or (office) the chosen person."""
    office = request.user.has_perm("planning.view_tour")
    if request.GET.get("person") and office:
        return get_object_or_404(Employee, pk=request.GET["person"])
    own = Employee.objects.filter(user=request.user).first()
    if own is None and office:
        return Employee.objects.filter(active=True).first()  # office without own day: first person
    return own


def _stop_context(stop):
    target = stop.building or stop.installation_order
    address = f"{target.street}, {target.zip_code} {target.city}"
    notes.attach_notes([stop])  # open notes of the object: problems reported from here are shown on the card
    visits.attach_attempts([stop], stop.tour.date)  # 2. Termin? what happened last time?
    stop.problems = [n for n in stop.notes if n.kind == "problem"]
    return {"s": stop, "address": address, "status_choices": BuildingStatus.choices, "reasons": REASONS, "reasons_dict": dict(REASONS),
            "navigation_url": "https://www.google.com/maps/dir/?api=1&destination=" + quote_plus(address)}


@login_required
def my_day(request):
    # 📱 only for Ableser/Monteur, Admin and roles that got "Eigenen Tagesplan sehen" in the admin
    if not request.user.has_perm("planning.view_own_tours"):
        raise PermissionDenied
    employee = _day_employee(request)
    if employee is None:
        messages.info(request, "Für deinen Benutzer sind keine Mitarbeiterdaten hinterlegt – bitte an einen Admin wenden.")
        return redirect("home")
    date = datetime.date.fromisoformat(request.GET["datum"]) if request.GET.get("datum") else timezone.localdate()
    tour = dayplan.tour_of(employee, date)
    stops = list(tour.stops.select_related("building", "installation_order", "done_by", "help_tour__employee").order_by("position")) if tour else []
    return render(request, "planning/my_day.html", {
        "employee": employee, "date": date, "today": timezone.localdate(), "tour": tour,
        "stops": [_stop_context(s) for s in stops],
        "map_data": tour_map_data(stops), "map_available": bool(stops) and bool(current_api_key()),
        "progress": dayplan.progress(stops),
        "previous_day": date - datetime.timedelta(days=1), "next_day": date + datetime.timedelta(days=1),
        "next_tour": Tour.objects.filter(services.tours_with(employee), date__gt=date).order_by("date").first(),
        "employees": Employee.objects.filter(active=True) if request.user.has_perm("planning.view_tour") else [],
        "can_work": tour is not None and dayplan.may_work_on(request.user, tour),
    })


@login_required
def object_visits(request, target, pk):
    """🧾 Bearbeitung of one building / order: every visit with its Ergebnis, and what comes next."""
    if not request.user.has_perm("planning.view_tour"):
        raise PermissionDenied
    building = get_object_or_404(Building, pk=pk) if target == "liegenschaft" else None
    order = get_object_or_404(InstallationOrder, pk=pk) if target == "auftrag" else None
    if building is None and order is None:
        raise PermissionDenied
    return render(request, "planning/_visits.html", {
        **visits.object_history(building, order), "target": target, "pk": pk, "reasons_dict": dict(REASONS),
        "close_picks": CLOSE_PICKS})


@require_POST
@permission_required("planning.process_visit", raise_exception=True)
def visit_close(request, pk):
    """🧾 box: '✓ geprüft' / '✓ abschließen – kein Nachtermin nötig' (with a reason) / open again."""
    visit = get_object_or_404(Visit, pk=pk)
    closed = request.POST.get("closed") == "1"
    try:
        visits.close(visit, request.user, closed, request.POST.get("closed_note", ""))
    except ValidationError as error:
        response = render(request, "core/_toast.html", {"message": error.messages[0], "error": True})
        response["HX-Reswap"] = "none"
        return response
    response = object_visits(request, "liegenschaft" if visit.building_id else "auftrag", visit.building_id or visit.installation_order_id)
    message = ("Wieder offen" if not closed else "Geprüft" if visit.outcome == "complete" else "Abgeschlossen – kein Nachtermin nötig")
    response.write(render(request, "core/_toast.html", {"message": message}).content)
    response["HX-Trigger"] = "buildings-changed, orders-changed, followup-changed"
    return response


def _stop_answer(request, stop, message, error=False):
    stops = list(stop.tour.stops.order_by("position"))
    response = render(request, "planning/_day_stop.html", {
        **_stop_context(stop), "can_work": True, "progress": dayplan.progress(stops), "oob_progress": True,
        "message": message, "error": error,
    })
    return response


@require_POST
@login_required
def stop_done(request, pk):
    stop = get_object_or_404(TourStop.objects.select_related("tour__employee", "building", "installation_order"), pk=pk)
    done = request.POST.get("done") == "1"
    _save_note_with_action(request, stop)
    try:
        if done:
            visits.report(stop, request.user, visits.COMPLETE)  # ✓ fertig (100 %)
        else:
            visits.undo(stop, request.user)
    except ValidationError as error:
        response = _stop_answer(request, stop, error.messages[0], error=True)
        response["HX-Reswap"] = "none"
        return response
    target = stop.building or stop.installation_order
    record(request.user, ActivityKind.FIELD, f"{'✓ fertig (100 %)' if done else '↺ Ergebnis zurückgesetzt'}: {target.street} "
           f"({stop.tour.employee} {day_label(stop.tour.date)})", tour=stop.tour, building=stop.building,
           order=stop.installation_order)
    return _stop_answer(request, stop, "Stopp erledigt" if done else "Stopp wieder offen")


def _save_note_with_action(request, stop):
    """The buttons send the note field along: a note typed just before tapping is never lost."""
    if "field_note" in request.POST and request.POST["field_note"].strip() != stop.field_note:
        dayplan.save_field_note(stop, request.POST["field_note"], request.user)
        target = stop.building or stop.installation_order
        record(request.user, ActivityKind.FIELD, f"Notiz vor Ort – {stop.tour.employee}, {target.street} "
               f"({day_label(stop.tour.date)}): {stop.field_note or '(gelöscht)'}", tour=stop.tour,
               building=stop.building, order=stop.installation_order)


@require_POST
@login_required
def stop_report(request, pk):
    """◐ teilweise erledigt / ✗ nicht erledigt (niemand da ...): what is still to do - required."""
    stop = get_object_or_404(TourStop.objects.select_related("tour__employee", "building", "installation_order"), pk=pk)
    outcome, todo, reason = request.POST.get("outcome", ""), request.POST.get("todo", ""), request.POST.get("reason", "")
    _save_note_with_action(request, stop)
    try:
        visit = visits.report(stop, request.user, outcome, todo, reason)
    except ValidationError as error:
        response = _stop_answer(request, stop, error.messages[0], error=True)
        response["HX-Reswap"] = "none"  # keep what was typed; only the message is shown
        return response
    target = stop.building or stop.installation_order
    what = visits.OUTCOME_LABELS[outcome] + (f" – {visits.REASONS[reason]}" if reason in visits.REASONS and outcome == visits.ABSENT else "")
    number = f"{visit.attempt}. Termin · " if visit else ""
    record(request.user, ActivityKind.FIELD, f"{what}: {target.street} ({stop.tour.employee} {day_label(stop.tour.date)}) · "
           f"{number}noch zu tun: {todo.strip()}", tour=stop.tour, building=stop.building, order=stop.installation_order)
    return _stop_answer(request, stop, "Ergebnis ans Büro gemeldet – Nachtermin wird geplant")


@require_POST
@login_required
def stop_note(request, pk):
    """Notiz vor Ort; with problem=1 also "⚠ Problem ans Büro" (a note the office sees at once)."""
    stop = get_object_or_404(TourStop.objects.select_related("tour__employee", "building", "installation_order"), pk=pk)
    before = stop.field_note
    dayplan.save_field_note(stop, request.POST.get("field_note", ""), request.user)
    target = stop.building or stop.installation_order
    where = f"{stop.tour.employee}, {target.street} ({day_label(stop.tour.date)})"
    if request.POST.get("problem") == "1":
        try:
            notes.field_problem(stop, stop.field_note, request.user)
        except ValidationError as error:
            return _stop_answer(request, stop, error.messages[0], error=True)
        record(request.user, ActivityKind.FIELD, f"⚠ Problem vor Ort – {where}: {stop.field_note}", tour=stop.tour,
               building=stop.building, order=stop.installation_order)
        return _stop_answer(request, stop, "Problem ans Büro gemeldet")
    if stop.field_note != before:
        record(request.user, ActivityKind.FIELD, f"Notiz vor Ort – {where}: {stop.field_note or '(gelöscht)'}",
               tour=stop.tour, building=stop.building, order=stop.installation_order)
    return _stop_answer(request, stop, "Notiz gespeichert")


@require_POST
@login_required
def stop_propose(request, pk):
    stop = get_object_or_404(TourStop.objects.select_related("tour__employee", "building", "installation_order"), pk=pk)
    if not stop.building or not dayplan.may_work_on(request.user, stop.tour):
        raise PermissionDenied
    propose_status(stop.building, request.POST.get("status", ""), request.user)  # also in the Verlauf (Status)
    return _stop_answer(request, stop, "Vorschlag an das Büro geschickt")


@login_required
def my_day_check(request, pk):
    """Polling every minute: reload the page only if the office changed the tour."""
    tour = Tour.objects.filter(pk=pk).first()
    if tour is None or str(tour.version) != request.GET.get("version"):
        response = HttpResponse("")
        response["HX-Refresh"] = "true"
        return response
    return HttpResponse(status=204)  # nothing changed


@login_required
def map_tile(request, z, x, y):
    """One map image for the TomTom maps (static/js/route_map.js).

    The browser asks OUR server; we fetch the image from TomTom with the key
    and pass it on. So the key never appears in the browser.
    """
    client = get_client()
    if client is None:
        return HttpResponse(status=404)
    try:
        png = client.map_tile(z, x, y)
    except TomTomError:
        return HttpResponse(status=404)  # the map just shows an empty square there
    response = HttpResponse(png, content_type="image/png")
    response["Cache-Control"] = "private, max-age=604800"  # the browser keeps it for a week
    return response


# --- 🤖 Automatic planning ---------------------------------------------------------------

def _autoplan_context(request):
    """The proposal from the session, with the names and addresses for the page."""
    proposal = request.session.get(services.AUTOPLAN_KEY)
    if not proposal:
        return None
    employees = Employee.objects.in_bulk([d["employee"] for d in proposal["days"]])
    buildings = Building.objects.in_bulk([s["building"] for d in proposal["days"] for s in d["stops"] if s.get("building")])
    orders = InstallationOrder.objects.in_bulk([s["order"] for d in proposal["days"] for s in d["stops"] if s.get("order")])
    days = []
    for index, day in enumerate(proposal["days"]):
        stops = []
        for s in day["stops"]:
            target = buildings.get(s.get("building")) if s["kind"] == StopKind.READING else orders.get(s.get("order"))
            if target is None:
                continue
            minutes = target.reading_minutes if s["kind"] == StopKind.READING else target.duration_minutes
            stops.append({"kind": s["kind"], "target": target, "minutes": minutes,
                          "label": target.file_number if s["kind"] == StopKind.READING else target.re_number})
        net = day["work"] + day["drive"]
        days.append({"index": index, "employee": employees.get(day["employee"]), "date": datetime.date.fromisoformat(day["date"]),
                     "stops": stops, "work": day["work"], "drive": day["drive"], "net": net,
                     "percent": min(100, round(net / 450 * 100)), "under": net < 360})
    days.sort(key=lambda d: (d["date"], d["employee"].short_name if d["employee"] else ""))
    return {**proposal, "day_cards": days, "stop_count": sum(len(d["stops"]) for d in days),
            "start_date": datetime.date.fromisoformat(proposal["start"]), "end_date": datetime.date.fromisoformat(proposal["end"])}


@permission_required(PLAN_PERMISSION, raise_exception=True)
def autoplan_page(request):
    """🤖 Automatisch planen: choose a period, see the proposals, change them, save them.

    FROZEN: only available when an admin switched it on (Verwaltung → Funktionen).
    """
    if not Features.load().autoplan:
        messages.info(request, "„Automatisch planen“ ist ausgeschaltet (Verwaltung → Funktionen).")
        return redirect("planning:calendar")
    today = timezone.localdate()
    monday = today + datetime.timedelta(days=7 - today.weekday())  # next Monday
    if request.method == "POST":
        action = request.POST.get("action")
        proposal = request.session.get(services.AUTOPLAN_KEY)
        if action == "compute":
            try:
                start = datetime.date.fromisoformat(request.POST["von"])
                end = datetime.date.fromisoformat(request.POST["bis"])
            except (KeyError, ValueError):
                messages.error(request, "Bitte einen gültigen Zeitraum wählen.")
                return redirect("planning:autoplan")
            if end < start or (end - start).days > 31:
                messages.error(request, "Zeitraum: höchstens ein Monat, „bis“ nach „von“.")
                return redirect("planning:autoplan")
            people = Employee.objects.filter(active=True)
            if request.POST.getlist("person"):
                people = people.filter(pk__in=request.POST.getlist("person"))
            kind = request.POST.get("art") if request.POST.get("art") in ("reading", "installation") else ""
            request.session[services.AUTOPLAN_KEY] = services.autoplan(people, max(start, today), end, kind)
        elif proposal and action in ("remove_stop", "remove_day", "open"):
            index = int(request.POST.get("day", -1))
            if not 0 <= index < len(proposal["days"]):
                return redirect("planning:autoplan")
            day = proposal["days"][index]
            if action == "remove_stop":
                stop = int(request.POST.get("stop", -1))
                if 0 <= stop < len(day["stops"]):
                    day["stops"].pop(stop)
                    services.autoplan_recount(day)
                if not day["stops"]:
                    proposal["days"].pop(index)
            elif action == "remove_day":
                proposal["days"].pop(index)
            elif action == "open":
                # this day into "Fahrplan prüfen" (exact TomTom times, change, create)
                request.session[services.DRAFT_KEY] = services.autoplan_draft(day)
                proposal["days"].pop(index)
                request.session[services.AUTOPLAN_KEY] = proposal
                return redirect("planning:draft")
            request.session[services.AUTOPLAN_KEY] = proposal
        elif proposal and action == "save_all":
            saved, problems = services.autoplan_save_all(proposal, request.user)
            del request.session[services.AUTOPLAN_KEY]
            messages.success(request, f"{len(saved)} Fahrpläne vorläufig gespeichert – bitte einzeln prüfen und bestätigen.")
            for problem in problems:
                messages.error(request, problem)
            first = min((t.date for t in saved), default=today)
            return redirect(f"{reverse('planning:calendar')}?datum={first.isoformat()}")
        elif action == "discard":
            request.session.pop(services.AUTOPLAN_KEY, None)
        return redirect("planning:autoplan")
    return render(request, "planning/autoplan.html", {
        "proposal": _autoplan_context(request),
        "default_start": monday, "default_end": monday + datetime.timedelta(days=4),
        "employees": Employee.objects.filter(active=True),
    })


# --- 🛰 Wer ist wo? (planning/whereabouts.py) ------------------------------------------------------

@permission_required("planning.view_tour", raise_exception=True)
def where_page(request):
    """Map: where each person should be at the chosen moment according to the Fahrplan (not GPS)."""
    from buildings.views import clean_url

    from . import whereabouts
    from .rules import whereabouts as where_rules

    now = timezone.localtime()
    today = now.date()
    try:
        day = datetime.date.fromisoformat(request.GET.get("datum", "")) if request.GET.get("datum") else today
    except ValueError:
        day = today
    default = min(max(now.hour * 60 + now.minute, where_rules.DAY_START), where_rules.DAY_END) if day == today else 10 * 60
    t = where_rules.clamp_time(request.GET.get("zeit", ""), default)
    kind = request.GET.get("art", "") if request.GET.get("art", "") in ("reading", "installation") else ""
    found = whereabouts.positions(day, t, kind, today)
    counts = where_rules.count_states(p.where.state for p in found)
    context = {
        "day": day, "t": t, "time": where_rules.clock(t), "art": kind, "positions": found, "today": today,
        "is_today": day == today, "now_time": where_rules.clock(now.hour * 60 + now.minute),
        "counts": [(state, where_rules.STATE_LABELS[state], counts[state]) for state in where_rules.STATE_ORDER if counts[state]],
        "map_data": whereabouts.map_data(found), "map_available": bool(current_api_key()),
        "slider_min": where_rules.DAY_START, "slider_max": where_rules.DAY_END,
        "previous_day": day - datetime.timedelta(days=1), "next_day": day + datetime.timedelta(days=1),
    }
    if request.htmx_target == "where-list":
        response = render(request, "planning/_where_list.html", context)
        response["HX-Push-Url"] = clean_url(request)
        return response
    return render(request, "planning/where.html", context)


# --- 📊 Übersicht (planning/overview.py) -------------------------------------------------------------

@permission_required("planning.view_tour", raise_exception=True)
def overview_page(request):
    """Dashboard: what is open now (tiles) and what happened in the chosen time range (charts)."""
    from buildings.views import clean_url

    from . import overview
    from .rules import overview as overview_rules

    today = timezone.localdate()
    period = overview_rules.chosen_period(request.GET.get("zeitraum", ""))
    kind = request.GET.get("art", "") if request.GET.get("art", "") in ("reading", "installation") else ""
    known = overview.stichtage()
    stichtag = overview_rules.chosen_stichtag(request.GET.get("stichtag", ""), set(known))
    context = {**overview.collect(period, kind, today, stichtag), "tiles": overview.headline(today), "period": period,
               "periods": overview_rules.PERIODS, "art": kind, "today": today, "stichtag": stichtag, "stichtage": known}
    if request.GET.get("format") == "xlsx":
        from .overview_excel import overview_workbook

        filters = (f"Zeitraum {context['start']:%d.%m.%Y} – {context['end']:%d.%m.%Y} · "
                   f"{ {'reading': 'nur Ablesung', 'installation': 'nur Montage'}.get(kind, 'Ablesung und Montage') } · "
                   f"Stichtag {stichtag:%d.%m.%Y}" if stichtag else
                   f"Zeitraum {context['start']:%d.%m.%Y} – {context['end']:%d.%m.%Y} · "
                   f"{ {'reading': 'nur Ablesung', 'installation': 'nur Montage'}.get(kind, 'Ablesung und Montage') } · alle Stichtage")
        response = HttpResponse(overview_workbook(context, context["tiles"], filters, request.user),
                                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"] = content_disposition_header(
            True, f"Uebersicht_{context['start']:%Y-%m-%d}_bis_{context['end']:%Y-%m-%d}.xlsx")
        return response
    if request.htmx_target == "ov-body":
        response = render(request, "planning/_overview_body.html", context)
        response["HX-Push-Url"] = clean_url(request)
        return response
    return render(request, "planning/overview.html", context)
