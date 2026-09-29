"""
"💶 Material & Kosten" in the Montage dashboard (Admin only, permission buildings.view_costs):
material and money the installations of a time window need - budget and ordering in time.
"""

import datetime
from urllib.parse import urlencode

from django.contrib.auth.decorators import permission_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .material import material_summary, set_article_price, set_category_price
from .material_excel import material_workbook
from .models import DeviceCategory
from .rules.material import DUE, GROUP_LABELS, OPEN, PLANNED, PRESETS, preset_window

COSTS = "buildings.view_costs"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _window(params):
    """(start, end, preset) from ?z=1w|2w|1m|3m or ?von=…&bis=… (own window)."""
    today = timezone.localdate()
    try:
        start = datetime.date.fromisoformat(params.get("von", ""))
        end = datetime.date.fromisoformat(params.get("bis", ""))
        if params.get("z", "frei") == "frei" and start <= end:
            return start, min(end, start + datetime.timedelta(days=731)), "frei"
    except ValueError:
        pass
    preset = params.get("z") if params.get("z") in PRESETS else "2w"
    return (*preset_window(preset, today), preset)


def _context(params):
    start, end, preset = _window(params)
    with_open = params.get("offen") == "1"
    summary = material_summary(start, end, with_open)
    categories = list(DeviceCategory.objects.all())
    return {
        "s": summary, "preset": preset, "presets": PRESETS, "with_open": with_open,
        "categories": categories, "labels": {c.code: c.label for c in categories},
        "groups": [(g, GROUP_LABELS[g]) for g in (PLANNED, DUE, OPEN)],
        "week_max": max((m for _, m in summary.by_week), default=0),
        "category_max": max((m for _, _, m in summary.by_category), default=0),
        "PLANNED": PLANNED, "DUE": DUE, "OPEN": OPEN,
        # the same window for the Excel download (also after a price change, which is a POST)
        "query": urlencode({"z": preset, "von": start.isoformat(), "bis": end.isoformat(), **({"offen": "1"} if with_open else {})}),
    }


@permission_required(COSTS, raise_exception=True)
def material_page(request):
    context = _context(request.GET)
    if request.GET.get("format") == "xlsx":
        s = context["s"]
        response = HttpResponse(material_workbook(s, context["labels"]), content_type=XLSX)
        response["Content-Disposition"] = f'attachment; filename="Material_{s.start:%Y-%m-%d}_bis_{s.end:%Y-%m-%d}.xlsx"'
        return response
    if request.htmx_target == "material-results":
        response = render(request, "orders/_material.html", context)
        response["HX-Push-Url"] = f"{request.path}?{context['query']}"
        return response
    return render(request, "orders/material.html", context)


@require_POST
@permission_required(COSTS, raise_exception=True)
def material_price(request):
    """A price was typed: category price or fixed article price (empty = category price again)."""
    try:
        if "category" in request.POST:
            if not request.user.has_perm("buildings.change_devicecategory"):
                return HttpResponse("Preise ändern darf deine Rolle nicht.", status=403)
            category = set_category_price(request.POST["category"], request.POST.get("price", ""))
            message = f"Preis {category.label} gespeichert"
        else:
            if not request.user.has_perm("buildings.change_articleprice"):
                return HttpResponse("Preise ändern darf deine Rolle nicht.", status=403)
            article = request.POST.get("article", "").strip()
            if not article:
                return HttpResponseBadRequest("Keine Artikel-Nr.")
            fixed = set_article_price(article, request.POST.get("description", ""), request.POST.get("price", ""))
            message = f"Artikelpreis {article} gespeichert" if fixed else f"{article}: wieder Preis der Kategorie"
    except (ValueError, DeviceCategory.DoesNotExist) as error:
        response = render(request, "core/_toast.html", {"message": str(error), "error": True})
        response["HX-Reswap"] = "none"
        return response
    response = render(request, "orders/_material.html", _context(request.POST))
    response.write(render(request, "core/_toast.html", {"message": message}).content)
    return response
