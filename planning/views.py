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
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST

from buildings.models import Building, BuildingStatus, InstallationOrder
from buildings.services import propose_status

from . import dayplan, services
from .calendar import calendar_events, free_day_events, tour_kind
from .display import preview_map_data, route_sketch, tour_map_data
from .excel import build_workbook
from .forms import DraftSettingsForm, PlanForm
from .models import Absence, Employee, StopKind, Tour, TourStop
from .rules.ordering import STRATEGIES
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
    settings_form = DraftSettingsForm(initial={"start": draft["start"], "break_minutes": draft["break"]})
    suggestions, too_long = services.draft_suggestions(draft, net_minutes=preview.day_plan.net_minutes)
    response = render(request, template, {"preview": preview, "draft": draft, "settings_form": settings_form,
                                          "suggestions": suggestions, "suggestions_too_long": too_long,
                                          "team_candidates": _team_candidates(draft),
                                          "strategies": STRATEGIES, "sketch": route_sketch(preview.stops),
                                          "map_data": preview_map_data(preview.stops), "map_available": preview.has_tomtom})
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
    if action in ("team_add", "team_remove", "team_split"):
        # 👥 Team for big objects (planning/services.py: set_team)
        try:
            message = services.set_team(
                current, add=int(request.POST.get("pk") or 0) if action == "team_add" else None,
                remove=int(request.POST.get("pk") or 0) if action == "team_remove" else None,
                split=(request.POST.get("split") == "1") if action == "team_split" else None)
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
    if not message and response.preview.time_notice:
        # information after each change while the day is longer than 7,5 h / shorter than 6 h
        message, info = f"ℹ ⏱ {response.preview.time_notice.text}", True
    if message:
        response.content += render(request, "core/_toast.html", {"message": message, "error": error, "info": info}).content
    return response


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
    stops = list(TourStop.objects.filter(tour=tour).select_related("building", "installation_order", "help_tour__employee")
                 .order_by("position"))
    helps = list(TourStop.objects.filter(kind=StopKind.HELP, help_tour=tour).select_related("tour__employee"))
    for stop in stops:
        stop.helpers = [h for h in helps if h.kind == StopKind.HELP and services.same_object(
            h, stop.building_id, stop.installation_order_id)] if stop.kind != StopKind.HELP else []
    # who can help at one object: everybody active who is not in this plan and not fixed in a team that day
    in_teams = Tour.objects.filter(date=tour.date, team__isnull=False).values("team")  # no NULLs in "NOT IN"
    helper_candidates = (Employee.objects.filter(active=True).exclude(pk__in=[p.pk for p in tour.people])
                         .exclude(pk__in=in_teams).exclude(pk__in=Absence.objects.filter(
                             start_date__lte=tour.date, end_date__gte=tour.date).values("employee")))
    return render(request, "planning/_tour_detail.html", {
        "tour": tour, "stops": stops, "helper_candidates": helper_candidates, "map_data": tour_map_data(stops), "map_available": bool(current_api_key()),
        # moving to another person: readers for readings, installers for installations, both for mixed plans
        "employees": _move_candidates(stops),
    })


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
    services.delete_tour(tour)
    response = render(request, "core/_toast.html", {"message": f"Fahrplan {label} gelöscht"})
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
    return {"s": stop, "address": address, "status_choices": BuildingStatus.choices,
            "navigation_url": "https://www.google.com/maps/dir/?api=1&destination=" + quote_plus(address)}


@login_required
def my_day(request):
    if not (request.user.has_perm("planning.view_own_tours") or request.user.has_perm("planning.view_tour")):
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


def _stop_answer(request, stop, message):
    stops = list(stop.tour.stops.order_by("position"))
    response = render(request, "planning/_day_stop.html", {
        **_stop_context(stop), "can_work": True, "progress": dayplan.progress(stops), "oob_progress": True,
        "message": message,
    })
    return response


@require_POST
@login_required
def stop_done(request, pk):
    stop = get_object_or_404(TourStop.objects.select_related("tour__employee", "building", "installation_order"), pk=pk)
    done = request.POST.get("done") == "1"
    dayplan.set_stop_done(stop, request.user, done)
    return _stop_answer(request, stop, "Stopp erledigt" if done else "Stopp wieder offen")


@require_POST
@login_required
def stop_note(request, pk):
    stop = get_object_or_404(TourStop.objects.select_related("tour__employee", "building", "installation_order"), pk=pk)
    dayplan.save_field_note(stop, request.POST.get("field_note", ""), request.user)
    return _stop_answer(request, stop, "Notiz gespeichert")


@require_POST
@login_required
def stop_propose(request, pk):
    stop = get_object_or_404(TourStop.objects.select_related("tour__employee", "building", "installation_order"), pk=pk)
    if not stop.building or not dayplan.may_work_on(request.user, stop.tour):
        raise PermissionDenied
    propose_status(stop.building, request.POST.get("status", ""), request.user)
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
