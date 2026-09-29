"""
"🔧 Montageaufträge" page (orders list of the Montage dashboard).

Same HTMX patterns as the building list: the filter form reloads only
#order-results, every edited cell saves itself and returns its row, the
checkbox adds the order to the selection for "Montage planen".
"""

from django.contrib.auth.decorators import permission_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from planning import services as planning
from planning.models import Employee

from .models import InstallationOrder, OrderPriority, OrderStatus
from .orders import MAX_INSTALLERS, OrderFilter, order_list_queryset, update_order
from .views import clean_url

VIEW = "buildings.view_installationorder"
MAX_ROWS = 300


def _summary(orders):
    """Numbers for the tiles above the table (orders: the filtered list)."""
    return {
        "total": len(orders),
        "open": sum(1 for o in orders if o.status in (OrderStatus.OPEN, OrderStatus.WORK_CARD)),
        "planned": sum(1 for o in orders if o.status == OrderStatus.PLANNED),
        "done": sum(1 for o in orders if o.status == OrderStatus.DONE),
        "without_date": sum(1 for o in orders if o.installation_date is None and o.status != OrderStatus.DONE),
        "conflicts": sum(1 for o in orders if o.has_open_conflict),
        "minutes": sum(o.duration_minutes for o in orders),
        "revisit": sum(1 for o in orders if o.last_outcome in ("partial", "absent") and not o.last_closed
                       and not o.revisit_planned),
    }


def _context(request):
    order_filter = OrderFilter(request.GET or None, queryset=order_list_queryset())
    orders = list(order_filter.qs)
    return {
        "filter": order_filter,
        "orders": orders[:MAX_ROWS],
        "count": len(orders),
        "all_count": InstallationOrder.objects.count(),
        "summary": _summary(orders),
        "selection": set(planning.get_order_selection(request.session)),
        **_row_context(),
        **planning.order_bar_context(request.session),
    }


def _row_context():
    return {
        "priorities": OrderPriority.choices,
        "statuses": OrderStatus.choices,
        "installers": list(Employee.objects.filter(can_install=True, active=True)),
        "max_installers": MAX_INSTALLERS,
    }


@permission_required(VIEW, raise_exception=True)
def order_list(request):
    context = _context(request)
    if request.htmx_target == "order-results":
        response = render(request, "orders/_results.html", context)
        response["HX-Push-Url"] = clean_url(request)
        return response
    return render(request, "orders/list.html", context)


def render_order_row(request, pk, opened=False, message="", error=""):
    order = get_object_or_404(order_list_queryset(), pk=pk)
    return render(request, "orders/_row_toggle.html", {
        "o": order, "open": opened, "message": message, "error": error,
        "selection": set(planning.get_order_selection(request.session)), **_row_context(),
    })


@permission_required(VIEW, raise_exception=True)
def order_row(request, pk):
    """▸ / ▾: the row with or without its detail row."""
    return render_order_row(request, pk, opened=request.GET.get("open") == "1")


@require_POST
@permission_required(VIEW, raise_exception=True)
def order_update(request, pk):
    order = get_object_or_404(InstallationOrder, pk=pk)
    try:
        field = update_order(order, request.POST, request.user)
    except ValidationError as error:
        response = render_order_row(request, pk, message=error.messages[0], error=True)
        response["HX-Reswap"] = "none"  # keep what the user typed; only the toast is shown
        return response
    response = render_order_row(request, pk, opened=request.POST.get("open") == "1",
                                message=f"{field} gespeichert · {order.re_number}")
    response["HX-Trigger"] = "orders-changed, conflicts-changed"
    return response


@require_POST
@permission_required("planning.add_tour", raise_exception=True)
def order_select(request):
    """Checkbox in a row: add / remove the order for "Montage planen"."""
    try:
        order_id = int(request.POST["order"])
    except (KeyError, ValueError):
        return HttpResponseBadRequest("Kein Auftrag angegeben.")
    planning.toggle_order_selection(request.session, order_id, "checked" in request.POST)
    return render(request, "orders/_plan_bar.html", planning.order_bar_context(request.session))


@require_POST
@permission_required("planning.add_tour", raise_exception=True)
def order_select_clear(request):
    planning.clear_order_selection(request.session)
    response = render(request, "orders/_plan_bar.html", planning.order_bar_context(request.session))
    response["HX-Refresh"] = "true"
    return response
