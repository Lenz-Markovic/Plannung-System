"""The company notice template: which boxes/fields get what (pure), and the filled Word file."""

import datetime
import re
import zipfile
from io import BytesIO

from documents.aushang_docx import build_docx
from documents.aushang_fields import Devices, devices_from_categories, devices_from_counts, notice_fields, time_text

WED = datetime.date(2026, 11, 4)
NINE_TO_ELEVEN = (datetime.time(9), datetime.time(11))


def test_reading_ticks_ablesung_and_the_devices_of_the_building():
    devices = devices_from_counts(hkv=40, kwz=10, wmz=0)
    fields = notice_fields("reading", WED, NINE_TO_ELEVEN, "4711-02", "Hauptstr. 1, 70372 Stuttgart", devices)
    assert fields.boxes == {"ablesung", "hkv", "wasser"}
    assert (fields.number, fields.weekday_name, fields.date, fields.time) == ("4711-02", "Mittwoch", "04.11.2026", "09:00 – 11:00 Uhr")


def test_smoke_detector_check_at_a_reading():
    fields = notice_fields("reading", WED, NINE_TO_ELEVEN, devices=devices_from_counts(hkv=1, rwm=5), rwm_check=True)
    assert {"wartung", "rwm"} <= fields.boxes
    no_check = notice_fields("reading", WED, NINE_TO_ELEVEN, devices=devices_from_counts(hkv=1, rwm=5))
    assert "rwm" not in no_check.boxes  # a plain reading does not touch the smoke detectors


def test_installation_montage_or_austausch_with_the_ordered_devices():
    devices = devices_from_categories(["EHKV", "MANSCH", "RWM"])
    assert devices == Devices(hkv=True, rwm=True)
    assert notice_fields("installation", WED, NINE_TO_ELEVEN, devices=devices).boxes == {"montage", "hkv", "rwm"}
    assert "austausch" in notice_fields("installation", WED, NINE_TO_ELEVEN, exchange=True).boxes


def test_sunday_is_not_in_the_weekday_list_of_the_template():
    assert notice_fields("reading", datetime.date(2026, 11, 8), None).weekday is None
    assert time_text(None) == "" and time_text((datetime.time(8), None)) == "ab 08:00 Uhr"


def test_word_file_fills_the_form_fields_of_the_template():
    a = notice_fields("reading", WED, NINE_TO_ELEVEN, "4711-02", "Hauptstr. 1 & 3, 70372 Stuttgart", Devices(hkv=True))
    b = notice_fields("installation", datetime.date(2026, 11, 6), NINE_TO_ELEVEN, "1234-01", "Königstr. 1", exchange=True)
    package = zipfile.ZipFile(BytesIO(build_docx([a, b])))
    xml = package.read("word/document.xml").decode()
    assert "4711-02" in xml and "Hauptstr. 1 &amp; 3, 70372 Stuttgart" in xml and "09:00 – 11:00 Uhr" in xml
    assert xml.count('<w:result w:val="2"/>') == 1 and xml.count('<w:result w:val="4"/>') == 1  # Mittwoch, Freitag
    assert xml.count('<w:checked w:val="1"/>') == 3  # Ablesung + HKV on page 1, Austausch on page 2
    assert xml.count("<w:pageBreakBefore/>") == 1
    # every bookmark once (Word does not like the same bookmark twice)
    starts = re.findall(r'<w:bookmarkStart w:id="(\d+)"', xml)
    ends = re.findall(r'<w:bookmarkEnd w:id="(\d+)"', xml)
    assert len(starts) == len(set(starts)) and sorted(starts) == sorted(ends)
