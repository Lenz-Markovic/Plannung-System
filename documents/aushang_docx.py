"""
Fill the company Word template for tenant notices (documents/vorlage/Aushang_Vorlage.dotx).

The template stays exactly as it is: the design is the full-page picture in
its header, the data goes into its own Word form fields (see aushang_fields.py).
Only the values of the fields change, so every field stays at its place.
Several notices = the page repeated, each on a new page, in ONE .docx file.

Only the standard library (zipfile + string edits of word/document.xml).
"""

import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from .aushang_fields import BOX_FIELDS

TEMPLATE = Path(__file__).parent / "vorlage" / "Aushang_Vorlage.dotx"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

TEXT_FIELDS = {"number": "Text12", "address": "Text2", "date": "Text5", "time": "Text4", "bottom": "Text10"}


def _field_start(xml, name):
    return xml.index(f'<w:name w:val="{name}"/>')


def _set_text(xml, name, value):
    """Replace the shown result of the text field `name` (the runs between 'separate' and its 'end')."""
    if not value:
        return xml  # keep the empty placeholder of the template (it keeps the spacing)
    start = _field_start(xml, name)
    sep = xml.index('w:fldCharType="separate"', start)
    first = xml.index("</w:r>", sep) + len("</w:r>")
    # Text2 has another field (Text11) inside: the result ends at the LAST 'end' before the next field starts
    depth, pos = 1, first
    while depth:
        nxt_end = xml.index('w:fldCharType="end"', pos)
        nxt_begin = xml.find('w:fldCharType="begin"', pos, nxt_end)
        if nxt_begin != -1:
            depth, pos = depth + 1, nxt_begin + 1
        else:
            depth, pos = depth - 1, nxt_end + 1
    end_run = xml.rindex("<w:r>", first, pos)
    rpr = re.findall(r"<w:rPr>.*?</w:rPr>", xml[sep - 400:sep])  # the format of the field
    run = f'<w:r>{rpr[-1] if rpr else ""}<w:t xml:space="preserve">{escape(value)}</w:t></w:r>'
    return xml[:first] + run + xml[end_run:]


def _set_checkbox(xml, name, checked):
    start = _field_start(xml, name)
    end = xml.index("</w:checkBox>", start)
    part = re.sub(r'<w:checked w:val="[01]"/>', f'<w:checked w:val="{int(checked)}"/>', xml[start:end])
    return xml[:start] + part + xml[end:]


def _set_dropdown(xml, name, index):
    start = _field_start(xml, name)
    end = xml.index("</w:ddList>", start)
    part = re.sub(r'<w:result w:val="\d+"/>', f'<w:result w:val="{index}"/>', xml[start:end])
    return xml[:start] + part + xml[end:]


def _drop_lonely_bookmark_ends(body):
    """A filled field can swallow a bookmark start (Text11 inside Text2): drop its end, too."""
    starts = set(re.findall(r'<w:bookmarkStart w:id="(\d+)"', body))
    return re.sub(r'<w:bookmarkEnd w:id="(\d+)"/>', lambda m: m.group(0) if m.group(1) in starts else "", body)


def fill_body(body, fields):
    """The page body (without sectPr) with the values of one NoticeFields."""
    for attr, name in TEXT_FIELDS.items():
        body = _set_text(body, name, getattr(fields, attr))
    body = _drop_lonely_bookmark_ends(body)
    for box, name in BOX_FIELDS.items():
        body = _set_checkbox(body, name, box in fields.boxes)
    if fields.weekday is not None:
        body = _set_dropdown(body, "Dropdown1", fields.weekday)
    return body


def _without_bookmarks(body):
    """Copies of the page must not repeat the bookmark names/ids of the form fields."""
    return re.sub(r"<w:bookmark(Start|End) [^>]*/>", "", body)


def build_docx(pages, template=TEMPLATE):
    """pages: list of NoticeFields -> bytes of one .docx (one A4 page per notice)."""
    source = zipfile.ZipFile(template)
    xml = source.read("word/document.xml").decode("utf-8")
    head, rest = xml.split("<w:body>", 1)
    body, tail = rest.rsplit("<w:sectPr", 1)
    bodies = []
    for i, fields in enumerate(pages):
        page = fill_body(body, fields)
        if i:
            page = _without_bookmarks(page)
            page = page.replace("<w:pPr>", "<w:pPr><w:pageBreakBefore/>", 1)  # every notice on its own page
        bodies.append(page)
    document = f"{head}<w:body>{''.join(bodies)}<w:sectPr{tail}"

    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "word/document.xml":
                data = document.encode("utf-8")
            elif item.filename == "[Content_Types].xml":  # a template (.dotx) becomes a normal document (.docx)
                data = data.replace(b"wordprocessingml.template.main+xml", b"wordprocessingml.document.main+xml")
            target.writestr(item, data)
    return out.getvalue()
