"""
Building list ("Liegenschaften-Dashboard").

How HTMX is used here (no own JavaScript needed):
- The filter form sends every change with hx-get to building_list.
  For an HTMX request we only return the part below the filters
  (_results.html); the browser swaps it in and updates the URL.
- The last table row "weitere laden" fetches the next page (building_rows)
  as soon as it becomes visible - like the endless scrolling in the prototype.
- The ▸ button in a row fetches the same row plus a detail row (building_row).
"""

from django.contrib.auth.decorators import permission_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from .display import building_schedule
from .filters import BuildingFilter, sort_buildings
from .selectors import building_list_queryset, building_summary, source_summary, with_schedule_details

PAGE_SIZE = 100


def filtered_buildings(request):
    """Apply the filters and the sorting from the URL. Used by all list views."""
    building_filter = BuildingFilter(request.GET, queryset=building_list_queryset())
    buildings = sort_buildings(building_filter.qs, request.GET.get("sort", ""))
    return building_filter, buildings


def clean_url(request):
    params = request.GET.copy()
    for key in [key for key, value in params.items() if value == ""]:
        del params[key]
    return f"{request.path}?{params.urlencode()}" if params else request.path


def page_rows(buildings, page_number):
    """One page of rows, with the schedule column prepared for each row."""
    page = Paginator(buildings, PAGE_SIZE).get_page(page_number)
    rows = list(with_schedule_details(page.object_list))
    for building in rows:
        building.schedule = building_schedule(building)
    return page, rows


@permission_required("buildings.view_building", raise_exception=True)
def building_list(request):
    building_filter, buildings = filtered_buildings(request)
    page, rows = page_rows(buildings, 1)
    context = {
        "filter": building_filter,
        "page": page,
        "rows": rows,
        "summary": building_summary(building_filter.qs),
        "sources": source_summary(),
    }
    if request.htmx_target == "results":
        response = render(request, "buildings/_results.html", context)
        # Show only the filters that are really set in the address bar
        # (?region=Region+Calw instead of ?q=&stichtag=&region=Region+Calw&...).
        response["HX-Push-Url"] = clean_url(request)
        return response
    return render(request, "buildings/list.html", context)


@permission_required("buildings.view_building", raise_exception=True)
def building_rows(request):
    """Next page of rows for the 'weitere laden' row."""
    _, buildings = filtered_buildings(request)
    page, rows = page_rows(buildings, request.GET.get("page", 2))
    return render(request, "buildings/_rows.html", {"page": page, "rows": rows})


@permission_required("buildings.view_building", raise_exception=True)
def building_row(request, pk):
    """One row, opened (?open=1: with detail row) or closed again."""
    building = get_object_or_404(with_schedule_details(building_list_queryset()), pk=pk)
    building.schedule = building_schedule(building)
    return render(request, "buildings/_row_toggle.html", {"b": building, "open": request.GET.get("open") == "1"})
