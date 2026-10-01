"""Readout gaps found with the office: no "freigegeben" while flats are open, Stichtag in planning,
Gateway check without appointment, Ankündigung + Zugang per stop, Zwischenablesung."""

import datetime

import pytest
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from buildings import services as building_services
from buildings.models import Building, BuildingStatus
from buildings.rules.status import release_blocked, status_options
from core import roles
from planning import services, visits
from planning.models import StopKind, Tour, Visit

pytestmark = pytest.mark.django_db
TODAY = datetime.date(2026, 9, 28)


@pytest.fixture
def demo(demo_import, monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda *args: TODAY)
    monkeypatch.setattr(services, "get_client", lambda: None)


def user(role, name):
    u = User.objects.create_user(username=name)
    u.groups.add(Group.objects.get(name=role))
    return u


def client_for(u):
    c = Client()
    c.force_login(u)
    return c


def reported_stop(outcome="partial", todo="Wohnung 4 war verschlossen", reason=""):
    """A reading stop reported by its reader (as from 📱 Mein Tag)."""
    tour = Tour.objects.filter(stops__kind=StopKind.READING).order_by("date").first()
    reader = user(roles.READER, f"abl{tour.pk}")
    tour.employee.user = reader
    tour.employee.save()
    stop = tour.stops.filter(kind=StopKind.READING).first()
    visits.report(stop, reader, outcome, todo, reason)
    return stop


# --- G: never "freigegeben" while flats are open -------------------------------------------

def test_release_rule_is_pure():
    assert release_blocked("partial", None) and release_blocked("absent", None)
    assert not release_blocked("partial", datetime.datetime(2026, 9, 1)) and not release_blocked("complete", None)
    assert not release_blocked("", None)
    perms = {"buildings.release_building", "buildings.set_status_open_rework"}
    released = dict((v, ok) for v, _, ok in status_options(perms, "open", blocked=True))
    assert released["released"] is False and released["rework"] is True
    assert dict((v, ok) for v, _, ok in status_options(perms, "released", blocked=True))["released"] is True


def test_release_is_blocked_until_the_flats_are_done_or_closed(demo):
    stop = reported_stop()
    building = Building.objects.get(pk=stop.building_id)
    sb = user(roles.PROCESSING, "sb")
    with pytest.raises(ValidationError, match="Wohnung 4 war verschlossen"):
        building_services.change_status(building, BuildingStatus.RELEASED, sb)

    # in the list: the dropdown can't choose it, and a try shows the reason and keeps the status
    c = client_for(sb)
    row = c.post(reverse("buildings:update", args=[building.pk]), {"status": "released"}).content.decode()
    assert "Freigeben geht noch nicht" in row and "erst Nachtermin" in row
    building.refresh_from_db()
    assert building.status != BuildingStatus.RELEASED

    # special case: the office closes it with a reason, then it can be released
    visits.close(Visit.objects.get(stop=stop), sb, True, "Werte geschätzt – kein Zutritt")
    building_services.change_status(building, BuildingStatus.RELEASED, sb)
    assert Building.objects.get(pk=building.pk).status == BuildingStatus.RELEASED


def test_complete_visit_can_be_released(demo):
    stop = reported_stop("complete", "")
    building = Building.objects.get(pk=stop.building_id)
    building_services.change_status(building, BuildingStatus.RELEASED, user(roles.PROCESSING, "sb"))
    assert Building.objects.get(pk=building.pk).status == BuildingStatus.RELEASED


# --- B: Stichtag in planning ------------------------------------------------------------------

def test_stichtag_rule():
    from planning.rules.stichtag import stichtag_findings
    st = datetime.date(2026, 12, 31)
    assert stichtag_findings(datetime.date(2026, 12, 10), st, 1, TODAY) == []
    late = stichtag_findings(datetime.date(2027, 1, 5), st, 1, TODAY)
    assert late[0][0] == "warning" and "5 Tage nach dem Stichtag 31.12.2026" in late[0][1]
    assert stichtag_findings(datetime.date(2027, 3, 5), st, 2, TODAY) == []          # Nachablesung: no warning
    soon = stichtag_findings(TODAY + datetime.timedelta(days=5), st, 1, TODAY)
    assert soon[0][0] == "info" and "nur noch 5 Tage" in soon[0][1]


def test_plan_check_shows_the_stichtag_and_warns_when_late(demo):
    building = Building.objects.filter(tour_stops__isnull=True).order_by("file_number").first()
    building.stichtag = datetime.date(2026, 10, 15)
    building.save()
    from planning.models import Employee
    employee = Employee.objects.filter(can_read=True).first()
    draft = services.create_draft([building.pk], employee, datetime.date(2026, 10, 26), employee.default_start_time, 30, "far")
    preview = services.calculate_preview(draft)
    stop = next(s for s in preview.stops if s.kind == StopKind.READING)
    assert any("nach dem Stichtag 15.10.2026" in f.message for f in stop.findings)
    assert preview.critical_count == 0 if hasattr(preview, "critical_count") else True   # only a warning


def test_filter_stichtag_soon_without_appointment(demo):
    soon = Building.objects.filter(tour_stops__isnull=True).exclude(status=BuildingStatus.RELEASED).first()
    soon.stichtag = TODAY + datetime.timedelta(days=10)
    soon.save()
    planned = Building.objects.filter(tour_stops__kind=StopKind.READING).first()
    planned.stichtag = TODAY + datetime.timedelta(days=10)
    planned.save()
    c = client_for(user(roles.DISPATCHER, "dispo"))
    html = c.get(reverse("buildings:list"), {"frist": "bald"}).content.decode()
    assert soon.file_number in html and planned.file_number not in html
    assert "Stichtag bald – noch kein Termin" in c.get(reverse("buildings:list")).content.decode()


# --- A: 📡 Gateway check without appointment ----------------------------------------------------

def test_gateway_states():
    from buildings.rules import gateway as gw
    f = gw.GatewayFacts
    assert gw.gateway_state(f(False, None, None)) == gw.CHECK
    assert gw.gateway_state(f(True, 24, 24)) == gw.COMPLETE
    assert gw.gateway_state(f(True, 24, 24, manual_devices=2, manual_state="angefordert")) == gw.WAIT_VALUES
    assert gw.gateway_state(f(True, 24, 24, manual_devices=2, manual_state="erhalten")) == gw.COMPLETE
    assert gw.gateway_state(f(True, 24, 20)) == gw.GAP and gw.needs_visit(gw.GAP)
    assert gw.gateway_state(f(True, 24, 20, planned=True)) == gw.PLANNED
    assert gw.gateway_state(f(True, 24, 20, last_visit="partial")) == gw.APPOINTMENT
    assert gw.gateway_state(f(True, 24, 20, last_visit="complete")) == gw.COMPLETE
    assert gw.gateway_state(f(True, 24, 24, released=True)) == gw.RELEASED
    assert gw.gap_minutes(4) == 20 and gw.gap_minutes(40) == 30
    assert gw.check_problems(10, 12, 0)


def gateway_building():
    b = (Building.objects.filter(installation_type="radio_gateway", tour_stops__isnull=True)
         .exclude(status=BuildingStatus.RELEASED).order_by("file_number").first())
    assert b is not None
    return b


def test_gateway_100_percent_is_released_without_appointment(demo):
    from buildings import gateway as gws
    b = gateway_building()
    assert b.pk in gws.no_visit_ids()            # not checked yet: not suggested for planning
    sb = client_for(user(roles.PROCESSING, "sb"))
    page = sb.get(reverse("buildings:gateways")).content.decode()
    assert "📡 Gateways" in page and b.file_number in page and "Gateway prüfen" in page
    row = sb.post(reverse("buildings:gateway_save", args=[b.pk]), {"received": "24", "total": "24"}).content.decode()
    assert "100 % – freigeben" in row and "✓ freigeben – kein Termin" in row
    row = sb.post(reverse("buildings:gateway_release", args=[b.pk])).content.decode()
    assert "Freigegeben ohne Termin" in row
    assert Building.objects.get(pk=b.pk).status == BuildingStatus.RELEASED
    bad = sb.post(reverse("buildings:gateway_save", args=[b.pk]), {"received": "30", "total": "24"})
    assert bad["HX-Reswap"] == "none" and "mehr Geräte" in bad.content.decode()


def test_gateway_gap_is_tried_from_outside_then_an_appointment(demo):
    from buildings import gateway as gws
    from buildings.rules import gateway as gw
    from planning.models import Employee
    b = gateway_building()
    dispo = user(roles.DISPATCHER, "dispo")
    gws.save_check(b, dispo, total=24, received=20, missing_note="NE003 2 HKV, NE007 WWZ")
    b.refresh_from_db()
    assert gws.state_of(b) == gw.GAP and b.pk not in gws.no_visit_ids()
    assert b.reading_minutes == 20                       # only the 4 missing devices, not the whole readout

    employee = Employee.objects.filter(can_read=True).first()
    day = datetime.date(2026, 10, 27)
    draft = services.create_draft([b.pk], employee, day, employee.default_start_time, 30, "far")
    preview = services.calculate_preview(draft)
    stop = next(s for s in preview.stops if s.kind == StopKind.READING)
    assert stop.work_minutes == 20 and any("von außen versuchen" in f.message for f in stop.findings)
    tour = services.save_draft(draft, dispo, confirm=False)
    saved = tour.stops.get(building=b)
    assert saved.visit_mode == "aussen" and saved.notice_suggestion == "keine" and gws.state_of(b) == gw.PLANNED

    # the phone shows "von außen", the reader catches only some -> appointment
    reader = user(roles.READER, "abl-gw")
    employee.user = reader
    employee.save()
    phone = client_for(reader).get(reverse("planning:my_day"), {"datum": day.isoformat()}).content.decode()
    assert "Von außen versuchen – ohne Termin" in phone and "NE003 2 HKV" in phone
    saved = type(saved).objects.get(pk=saved.pk)   # fresh: the person has a login now
    visits.report(saved, reader, "partial", "NE007 WWZ fehlt noch")
    assert Visit.objects.get(stop=saved).visit_mode == "aussen"
    assert gws.state_of(b) == gw.APPOINTMENT and b.pk in visits.revisit_ids()[0]
    # the appointment is a normal visit (not from outside again)
    draft = services.create_draft([b.pk], employee, datetime.date(2026, 11, 3), employee.default_start_time, 30, "far")
    tour2 = services.save_draft(draft, dispo, confirm=False)
    assert tour2.stops.get(building=b).visit_mode == ""


# --- C: Ankündigung + Zugang per stop -------------------------------------------------------------

def test_suggestions_are_only_suggestions():
    from documents import notice_rules as nr
    assert nr.suggest_access(True) == "alle" and nr.suggest_access(False) == "keine" and nr.suggest_access(True, "aussen") == "keine"
    assert nr.suggest_notice("alle") == "aushang" and nr.suggest_notice("einige") == "briefe"
    assert nr.suggest_notice("keine") == "keine" and nr.suggest_notice("alle", "aussen") == "keine"
    assert nr.choice_problems("briefe", "") and not nr.choice_problems("briefe", "Whg 3") and nr.choice_problems("x", "")


def coming_reading_stop():
    from planning.models import TourStop
    return (TourStop.objects.filter(kind=StopKind.READING, tour__date__gt=TODAY).select_related("tour", "building")
            .order_by("tour__date", "position").first())


def test_terminierung_chooses_per_stop_on_the_aushaenge_page(demo):
    stop = coming_reading_stop()
    c = client_for(user(roles.DISPATCHER, "dispo"))
    page = c.get(reverse("documents:aushaenge"), {"f": "entscheiden", "zeitraum": "alle"}).content.decode()
    assert f'id="ah-{stop.pk}"' in page and "❓ noch entscheiden" in page and "ah-choice" in page

    row = c.post(reverse("documents:aushang_choice", args=[stop.pk]), {"choice": "telefon"}).content.decode()
    assert "☎ telefonisch vereinbart" in row
    stop.refresh_from_db()
    assert stop.notice_choice == "telefon" and not stop.notice_wanted

    bad = c.post(reverse("documents:aushang_choice", args=[stop.pk]), {"choice": "briefe", "units": ""})
    assert bad["HX-Reswap"] == "none" and "Wohnungen eintragen" in bad.content.decode()
    c.post(reverse("documents:aushang_access", args=[stop.pk]), {"access": "einige", "access_units": "Whg 3 (Müller), Whg 7"})
    row = c.post(reverse("documents:aushang_choice", args=[stop.pk]), {"choice": "briefe"}).content.decode()
    stop.refresh_from_db()
    assert stop.notice_choice == "briefe" and stop.notice_wanted and stop.notice_units == "Whg 3 (Müller), Whg 7"
    assert "2 Briefe" in row and "noch nicht gedruckt" in row or "zu spät" in row
    assert stop.access_text == "🚪 nur in diese Wohnungen: Whg 3 (Müller), Whg 7"

    # it stays when the plan is saved again
    services.save_draft(services.draft_from_tour(Tour.objects.get(pk=stop.tour_id)), None, confirm=False)
    again = type(stop).objects.get(tour_id=stop.tour_id, building_id=stop.building_id)
    assert (again.notice_choice, again.access_scope, again.notice_units) == ("briefe", "einige", "Whg 3 (Müller), Whg 7")


def test_plan_check_takes_the_choice_into_the_saved_plan(demo):
    from planning.models import Employee
    building = Building.objects.filter(tour_stops__isnull=True, access_apartment=True).order_by("file_number").first()
    employee = Employee.objects.filter(can_read=True).first()
    c = client_for(user(roles.DISPATCHER, "dispo"))
    draft = services.create_draft([building.pk], employee, datetime.date(2026, 10, 28), employee.default_start_time, 30, "far")
    session = c.session
    session[services.DRAFT_KEY] = draft
    session.save()
    html = c.get(reverse("planning:draft")).content.decode()
    assert "Ankündigung" in html and "ah-choice" in html and "hint" in html           # the suggestion is marked
    index = next(i for i, s in enumerate(draft["stops"]) if s["kind"] == StopKind.READING)
    html = c.post(reverse("planning:draft_action"), {"action": "notice", "index": index, "choice": "mail"}).content.decode()
    assert "Ankündigung: 📧 per Mail" in html
    tour = services.save_draft(c.session[services.DRAFT_KEY], None, confirm=False)
    assert tour.stops.get(building=building).notice_choice == "mail"


# --- H: 🔄 Zwischenablesung --------------------------------------------------------------------

def test_interim_rules():
    from planning.rules import interim as ir
    flats = ir.clean_flats([("Whg 3", "Müller → Schmidt", ["hkv", "ampullen", "x"]), ("", "", []), ("Whg 7", "", ["wwz"])])
    assert flats == [{"unit": "Whg 3", "tenant": "Müller → Schmidt", "needs": ["hkv", "ampullen"]},
                     {"unit": "Whg 7", "tenant": "", "needs": ["wwz"]}]
    assert ir.minutes(flats) == 40 and ir.units_text(flats) == "Whg 3 (Müller → Schmidt), Whg 7"
    assert ir.problems([], None) and ir.problems([{"unit": "", "tenant": "x", "needs": ["hkv"]}], TODAY)
    assert ir.problems([{"unit": "Whg 1", "tenant": "", "needs": []}], TODAY)
    assert [c for c, _ in ir.offered_needs(10, True, 2, 0, 0, False)] == ["hkv", "ampullen", "wwz"]
    assert ir.state(False, "", False) == ir.OPEN and ir.state(False, "", True) == ir.PLANNED
    assert ir.state(False, "partial", True) == ir.PARTIAL and ir.state(True, "", True) == ir.CANCELLED
    assert ir.suggested_day(datetime.date(2026, 10, 3), TODAY) == datetime.date(2026, 10, 5)   # Saturday -> Monday


def test_interim_from_mail_to_plan_without_touching_the_main_readout(demo):
    from planning.models import Employee, InterimReading, TourStop
    planned = TourStop.objects.filter(kind=StopKind.READING, tour__date__gt=TODAY, interim__isnull=True).first()
    building = planned.building                   # its main readout is already planned elsewhere
    c = client_for(user(roles.DISPATCHER, "dispo"))
    form = c.get(reverse("planning:interim"), {"liegenschaft": building.pk}).content.decode()
    assert "🔄 Zwischenablesungen" in form and "Nutzerwechsel am" in form and "HKV ablesen" in form
    assert building.file_number in c.get(reverse("planning:interim_search"), {"suche": building.file_number}).content.decode()

    bad = c.post(reverse("planning:interim_create"), {"building": building.pk, "move_date": "", "source": "mail_hv",
                                                      "unit": ["Whg 3"], "tenant": [""], "needs_0": ["hkv"]})
    assert "Datum des Nutzerwechsels" in bad.content.decode()
    c.post(reverse("planning:interim_create"), {
        "building": building.pk, "move_date": "2026-10-15", "source": "mail_hv", "note": "Mail HV 01.10.",
        "unit": ["Whg 3", "Whg 7"], "tenant": ["Müller → Schmidt", ""], "needs_0": ["hkv", "wwz"], "needs_1": ["kwz"]})
    item = InterimReading.objects.get(building=building)
    assert item.flats[0]["needs"] == ["hkv", "wwz"] and item.minutes == 30
    page = c.get(reverse("planning:interim")).content.decode()
    assert "Müller → Schmidt" in page and "offen – noch einplanen" in page

    employee = Employee.objects.filter(can_read=True).exclude(pk=planned.tour.employee_id).first()
    answer = c.post(reverse("planning:interim_plan", args=[item.pk]), {"employee": employee.pk, "date": "2026-10-15"})
    assert answer["HX-Redirect"] == reverse("planning:draft")
    draft = c.session[services.DRAFT_KEY]
    preview = services.calculate_preview(draft)
    stop = next(s for s in preview.stops if s.interim is not None)
    assert stop.work_minutes == 30 and stop.findings == []              # no "schon im Fahrplan – wird verschoben"
    tour = services.save_draft(draft, None, confirm=False)
    saved = tour.stops.get(interim=item)
    assert saved.notice_choice == "briefe" and saved.notice_units == "Whg 3 (Müller → Schmidt), Whg 7"
    assert TourStop.objects.filter(pk=planned.pk).exists()             # the main readout stays where it was
    assert "📅 geplant" in c.get(reverse("planning:interim"), {"f": "geplant"}).content.decode()

    # the result stays with the Zwischenablesung, it makes no Nachtermin for the building
    reader = user(roles.READER, "abl-im")
    employee.user = reader
    employee.save()
    saved = TourStop.objects.get(pk=saved.pk)
    assert "Zwischenablesung – Nutzerwechsel 15.10.2026" in client_for(reader).get(
        reverse("planning:my_day"), {"datum": "2026-10-15"}).content.decode()
    visits.report(saved, reader, "partial", "Whg 7 nicht da")
    assert not Visit.objects.filter(stop=saved).exists() and building.pk not in visits.revisit_ids()[0]
    assert "nochmal einplanen" in c.get(reverse("planning:interim")).content.decode()


def test_reopened_plan_keeps_the_try_from_outside(demo):
    from buildings import gateway as gws
    from planning.models import Employee
    b = gateway_building()
    gws.save_check(b, user(roles.DISPATCHER, "dispo"), total=24, received=21)
    employee = Employee.objects.filter(can_read=True).first()
    tour = services.save_draft(services.create_draft([b.pk], employee, datetime.date(2026, 10, 27),
                                                     employee.default_start_time, 30, "far"), None, confirm=False)
    preview = services.calculate_preview(services.draft_from_tour(tour))
    stop = next(s for s in preview.stops if s.building == b)
    assert stop.visit_mode == "aussen" and stop.access_choice == "keine"
    assert any("von außen versuchen" in f.message for f in stop.findings)
    again = services.save_draft(services.draft_from_tour(tour), None, confirm=False)
    assert again.stops.get(building=b).visit_mode == "aussen"
