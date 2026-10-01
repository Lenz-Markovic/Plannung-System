"""📡 Gateways: the office checks what the gateway received - 100 % = freigeben without an appointment,
gaps = try from outside (no appointment, only the missing devices), then an appointment."""

from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from . import gateway
from .models import Building, BuildingStatus
from .rules import gateway as rules

FILTERS = [("offen", "zu tun"), (rules.CHECK, "📡 prüfen"), (rules.GAP, "📡 Lücke"), (rules.APPOINTMENT, "📅 Termin nötig"),
           (rules.WAIT_VALUES, "✉ Werte angefordert"), (rules.COMPLETE, "✓ 100 %"), (rules.PLANNED, "🚗 geplant"),
           (rules.RELEASED, "✓ freigegeben"), ("alle", "alle")]
OPEN = {rules.CHECK, rules.GAP, rules.APPOINTMENT, rules.WAIT_VALUES, rules.COMPLETE}


def _number(value):
    value = (value or "").strip()
    if not value:
        return None
    if not value.isdigit():
        raise ValidationError("Bitte nur ganze Zahlen eintragen.")
    return int(value)


def _rows(request, buildings):
    from planning.services import get_selection

    found = gateway.states(buildings)
    ticked = set(get_selection(request.session))
    rows = []
    for b in buildings:
        b.gw_state = found.get(b.pk, rules.CHECK)
        b.gw_label = rules.LABELS.get(b.gw_state, "")
        b.gw_total_hint = b.gateway_total if b.gateway_total is not None else rules.radio_devices(
            b.hkv_count, b.wmz_count, b.wwz_count, b.kwz_count)
        b.gw_minutes = rules.gap_minutes(b.gateway_missing) if b.gateway_missing else 0
        b.gw_ticked = b.pk in ticked
        rows.append(b)
    return sorted(rows, key=lambda b: (rules.ORDER.index(b.gw_state) if b.gw_state in rules.ORDER else 99,
                                       b.stichtag, b.file_number))


@permission_required("buildings.view_building", raise_exception=True)
def gateway_list(request):
    from planning.services import plan_bar_context

    chosen = request.GET.get("f", "offen")
    chosen = chosen if chosen in dict(FILTERS) else "offen"
    query = request.GET.get("q", "").strip().lower()
    all_rows = _rows(request, list(gateway.gateway_buildings().select_related("property_manager")))
    counts = {key: sum(1 for b in all_rows if (b.gw_state in OPEN if key == "offen" else key == "alle" or b.gw_state == key))
              for key, _ in FILTERS}
    rows = [b for b in all_rows if chosen == "alle" or (b.gw_state in OPEN if chosen == "offen" else b.gw_state == chosen)]
    if query:
        rows = [b for b in rows if query in f"{b.file_number} {b.street} {b.zip_code} {b.city}".lower()]
    context = {"rows": rows, "filters": [(k, label, counts[k]) for k, label in FILTERS], "chosen": chosen, "q": query,
               "manual_states": rules.MANUAL_STATES, **plan_bar_context(request.session)}
    if request.htmx_target == "gw-rows":
        return render(request, "buildings/_gateway_rows.html", context)
    return render(request, "buildings/gateways.html", context)


def _row(request, building, message="", error=False):
    building = Building.objects.select_related("property_manager").get(pk=building.pk)
    row = _rows(request, [building])[0]
    return render(request, "buildings/_gateway_row.html", {"b": row, "manual_states": rules.MANUAL_STATES,
                                                            "message": message, "error": error})


@require_POST
@permission_required("buildings.view_building", raise_exception=True)
def gateway_save(request, pk):
    building = get_object_or_404(Building, pk=pk)
    try:
        state = gateway.save_check(building, request.user, _number(request.POST.get("total")),
                                   _number(request.POST.get("received")), request.POST.get("missing", ""),
                                   _number(request.POST.get("manual")) or 0, request.POST.get("manual_state", ""))
    except (ValidationError, PermissionDenied) as error:
        text = " ".join(error.messages) if isinstance(error, ValidationError) else str(error)
        response = _row(request, building, text, error=True)
        response["HX-Reswap"] = "none"   # keep what was typed, only show the reason
        return response
    message = {rules.COMPLETE: "100 % – kann freigegeben werden", rules.GAP: "Lücke – von außen versuchen (ohne Termin)",
               rules.WAIT_VALUES: "Funk vollständig – Werte ohne Funk angefordert"}.get(state, "Gespeichert")
    response = _row(request, building, message)
    response["HX-Trigger"] = "buildings-changed"
    return response


@require_POST
@permission_required("buildings.view_building", raise_exception=True)
def gateway_release(request, pk):
    """✓ 100 % received (and the values without radio are there): freigeben - no appointment."""
    from .services import change_status

    building = get_object_or_404(Building, pk=pk)
    if gateway.state_of(building) != rules.COMPLETE:
        return _row(request, building, "Erst wenn alles da ist (100 % und Werte ohne Funk erhalten).", error=True)
    try:
        change_status(building, BuildingStatus.RELEASED, request.user)
    except ValidationError as error:
        return _row(request, building, " ".join(error.messages), error=True)
    except PermissionDenied:   # only Sachbearbeitung / Admin may release
        return _row(request, building, "Freigeben darf deine Rolle nicht – bitte die Sachbearbeitung.", error=True)
    response = _row(request, building, f"Freigegeben ohne Termin · {building.file_number}")
    response["HX-Trigger"] = "buildings-changed, deadlines-changed"
    return response
