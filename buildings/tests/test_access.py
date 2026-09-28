"""Access detection. Expected values were calculated with the prototype's zugangErkennen()."""

import pytest

from buildings.rules.access import detect_access


@pytest.mark.parametrize(
    "reading_type, remark, apartment, room, units, reasons, only_type",
    [
        ("MANU - Betreten der Wohnung", "Zugang zu HR über NE003. Aushang an Hausverwaltung.",
         True, True, ["NE003"], ["Ableseart: Wohnungen betreten", "Zugang nur über eine Wohnung"], False),
        ("FMAN - Betreten der Wohnung, Funk parall", "20 x RWM Funktionsprüfung. Aushang durch uns.",
         True, False, [], ["Ableseart: Wohnungen betreten", "RWM-Prüfung in den Wohnungen"], False),
        ("FSON - Funk Walk-by Sontex", "Zusammen mit AZ 8059 ablesen.", False, False, [], [], False),
        ("MANU - Betreten der Wohnung", "Zusammen mit AZ 8149 ablesen.",
         True, False, [], ["Ableseart: Wohnungen betreten"], True),
        ("MANU - Betreten der Wohnung", "NE002: Zähler manuell ablesen, Funk defekt.",
         True, False, ["NE002"], ["Ableseart: Wohnungen betreten", "Geräte manuell ablesen", "Aufgabe in einzelnen Wohnungen"], False),
        ("FAMS - Funk AMR Sontex", "WMZ im Heizraum Haus 2.", False, True, [], [], False),
        ("-", "17 x RWM Funktionsprüfung. Schlüssel für Heizraum beim Hausmeister.",
         True, True, [], ["RWM-Prüfung in den Wohnungen"], False),
    ],
)
def test_matches_prototype(reading_type, remark, apartment, room, units, reasons, only_type):
    access = detect_access(reading_type, remark)
    assert (access.apartment, access.room, access.units, access.reasons, access.only_reading_type) == (
        apartment, room, units, reasons, only_type)


def test_key_and_announcement_sentences():
    access = detect_access("Funkablesung", "Ablesung im Januar. Schlüssel für Heizraum beim Hausmeister. Termin vorher informieren!")
    assert access.key_hint == "Schlüssel für Heizraum beim Hausmeister."
    assert access.announcement_hint == "Termin vorher informieren!"


def test_unit_numbers_in_different_spellings():
    access = detect_access("", "004/006: HKV prüfen. Nutzer 12 fragen. NE 7 ablesen.")
    assert access.units == ["NE004", "NE006", "NE007", "NE012"]


def test_text_for_plan_and_print():
    access = detect_access("MANU - Betreten der Wohnung", "Zugang zu HR über NE003.")
    assert access.text() == ("🔑 Zugang zur Wohnung notwendig (NE003): Ableseart: Wohnungen betreten, "
                             "Zugang nur über eine Wohnung · 🚪 Zugang Heizraum/Keller")


@pytest.mark.django_db
def test_update_access_command_keeps_other_fields():
    import datetime
    import io

    from django.core.management import call_command

    from buildings.models import Building

    building = Building.objects.create(source_system="bfw_main", file_number="1", stichtag=datetime.date(2026, 12, 31),
                                       status="rework", reading_type="MANU - Betreten der Wohnung", remark="NE004: HKV prüfen.")
    call_command("update_access", stdout=io.StringIO())
    building.refresh_from_db()
    assert building.access_apartment and building.access_units == ["NE004"] and building.status == "rework"
