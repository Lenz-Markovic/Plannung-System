"""Excel of the 🗺 Aushang-Route: one row per house, distance / drive to the next one, totals (openpyxl)."""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

HEAD = PatternFill("solid", fgColor="1F4E8C")
START = PatternFill("solid", fgColor="D9EAD3")     # green
STOP = PatternFill("solid", fgColor="DDE7F6")      # blue
END = PatternFill("solid", fgColor="F4CCCC")       # red
WARN = PatternFill("solid", fgColor="FCE5B3")      # amber: warning
TOTAL = PatternFill("solid", fgColor="E8EEF7")
THIN = Border(bottom=Side(style="thin", color="BBBBBB"))


def _clock(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def route_workbook(context):
    route = context["route"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Aushang-Route"
    who = f" · {context['person']}" if context.get("person") else ""
    ws["A1"] = f"Aushang-Route {context['day']:%d.%m.%Y}{who}"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = (f"Start {_clock(context['start'].hour * 60 + context['start'].minute)} "
                f"{'zu Hause' if context.get('from_home') else 'am ersten Haus'} · {len(route)} Häuser · "
                f"{context['papers']} Aushänge/Briefe · Fahrzeiten {'TomTom' if context.get('all_tomtom') else 'geschätzt'}")
    ws["A2"].font = Font(italic=True, color="555555")
    head = ["Nr.", "Straße", "PLZ / Ort", "Ankunft", "Was", "für Termin", "km zum nächsten", "Fahrt zum nächsten (min)",
            "Hinweise", "✓"]
    ws.append([])
    ws.append(head)
    for cell in ws[4]:
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), HEAD
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    last = len(route) - 1
    for i, s in enumerate(route):
        following = route[i + 1] if i < last else None
        what = "; ".join(f"{p[0]}: {p[1]}" for p in s.papers)
        dates = ", ".join(sorted({f"{p[2]:%d.%m.} {p[3]}" for p in s.papers}))
        notes = []
        if s.point_source != "tomtom":
            notes.append("Adresse nur ungefähr (PLZ-Mitte)" if s.point else "Adresse ohne Position")
        if following is not None and not following.drive_from_tomtom:
            notes.append("Fahrzeit geschätzt")
        street = s.target.street
        place = f"{s.target.zip_code} {s.target.city}"
        ws.append([s.n, street, place, s.arrive_text, what, dates,
                   float(following.drive_km) if following and following.drive_km is not None else None,
                   following.drive_minutes if following else None, ", ".join(notes), "☐"])
        line = ws.max_row
        fill = START if i == 0 else (END if i == last else STOP)
        for cell in ws[line]:
            cell.border = THIN
            cell.fill = fill
            cell.alignment = Alignment(vertical="top", wrap_text=True)
        if notes:
            ws.cell(line, 9).fill = WARN
    ws.append(["", "Gesamt", f"{len(route)} Häuser", f"Ende {context['end_text']}", f"{context['papers']} Aushänge/Briefe",
               "", float(context["total_km"]), context["total_drive"], f"Arbeit ca. {context['total_work']} min", ""])
    for cell in ws[ws.max_row]:
        cell.font, cell.fill = Font(bold=True), TOTAL
    for column, width in zip("ABCDEFGHIJ", [5, 28, 22, 9, 42, 20, 10, 12, 28, 4]):
        ws.column_dimensions[column].width = width
    ws.freeze_panes = "A5"
    ws.page_setup.orientation = "landscape"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
