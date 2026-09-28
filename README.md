# Planungssystem Ablesung und Montage (Prototyp)

Django 5 + HTMX + Bootstrap. Source of truth for requirements:
`Systembeschreibung_Planungssystem_DE.docx`. The HTML files in
`docs/prototype/` are only a reference (workflows, business rules) and the
source for the demo data import.

## Setup (once)

```bash
python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # then edit .env (secret key, TomTom key)
python manage.py migrate
python manage.py setup_roles
python manage.py createsuperuser     # your own admin account
python manage.py create_demo_users --password "Test-Passwort-2026"   # optional, local only
python manage.py import_prototype    # demo data from docs/prototype/
```

## Daily use

```bash
source .venv/bin/activate
python manage.py runserver           # http://127.0.0.1:8000
pytest                               # run all tests
```

After changing a `models.py`: `python manage.py makemigrations` and then
`python manage.py migrate`.

## Demo data import

`python manage.py import_prototype` reads the demo data from the two HTML
files in `docs/prototype/` (240 buildings, 70 installation orders with their
contract lines, 11 readers/installers). Running it again updates instead of
duplicating; `--flush` deletes buildings, orders and tours first.
Planned days from the prototype become *provisional* tours without drive
times: they have to be recalculated with TomTom before they can be confirmed.
Imported readers get a user without a password; an admin sets one in
`/admin/` if a reader should log in.

## Building list (Liegenschaften-Dashboard)

`/liegenschaften/`: same layout as the Deckblätter prototype (KPI tiles,
filters, table with detail rows). Filtering and sorting run on the server;
HTMX swaps only the results and keeps the filters in the URL. Rows are
loaded 100 at a time while scrolling. Readers (role Ableser/Monteur) cannot
open the list; they will get their own day plan.

## Status workflow and 14-day deadline

- Status (offen / Nacharbeit / freigegeben), property manager, note and the
  received date of the cost documents are edited directly in the table.
  Who may set which status: `buildings/rules/status.py` (spec section 5).
- 14-day rule: `documents/rules.py`. The deadline starts when the documents
  arrive and restarts on every status change or new appointment.
- The warning pop-up (`templates/documents/_warning.html`) is checked on
  every page load, every 30 seconds and right after changes. It has no
  close button; it disappears only when the building is replanned or its
  status changes. Red = no appointment, yellow = appointment exists.
  ✕ / "Später erinnern" (15 min, 1 hour, tomorrow morning) only puts it
  aside; a reminder bar in the navigation stays visible until it returns.
- `/unterlagen/`: list of all received documents with their deadlines.

## Tour planning

1. Tick buildings in the list → "🗺 Fahrplan erstellen (n)".
2. Dialog: reader, day, start, break, order ("weitester Termin zuerst" /
   "kürzeste Gesamtstrecke").
3. Preview `/planung/entwurf/`: times, driving times, conflicts, rules;
   ▲ ▼ ✕ and new sorting recalculate at once. Nothing is saved yet.
4. "✓ Fahrplan übernehmen" only with real TomTom times and ≤ 7.5 h net;
   otherwise "Vorläufig speichern".

Rules: `planning/rules/` (5-minute rounding, working time and break,
ordering) and `conflicts/rules.py` (`planning_findings`). TomTom is only
called from `planning/tomtom.py` with `TOMTOM_API_KEY` from `.env`; every
address is geocoded once and stored. Without a key the preview uses
estimates (postcode centres from the prototype).
`python manage.py check_tomtom` checks the key step by step (never prints it).
After changing `.env`, restart the server.

## Calendar

`/planung/kalender/`: FullCalendar (CDN) with month, week and list view,
one event per tour in the person's colour (⏳ vorläufig, ⟳ neu rechnen,
⚠ Montage-Konflikt), absences as background. Click = side panel with the
stops ("Neu rechnen", "Verschieben" to another day/person, "Löschen").
Drag & drop to another day creates a draft for the new day; the preview
recalculates it (TomTom with the new day's traffic) and only "übernehmen"
moves the tour. Readers only see their own tours and cannot move them.
The only JavaScript is `static/js/calendar.js` (commented).

## Project layout

| Folder | Contents |
|---|---|
| `config/` | settings (read from `.env`), URLs |
| `core/` | shared base models, roles (`core/roles.py`), login, start page, user admin |
| `buildings/` | buildings, property managers, installation orders, prototype import (`importers/`) |
| `planning/` | employees, absences, tours and stops (+ TomTom client, working-time rules later) |
| `conflicts/` | stored conflict results (+ rules later) |
| `documents/` | cover sheets, received cost documents / 14-day deadline |
| `templates/` | HTML templates (German UI) |

Business rules are pure Python functions in `<app>/rules/` with tests in
`<app>/tests/`. They never import views or touch the request.

## Roles

Defined in `core/roles.py` (spec section 5). `setup_roles` adds missing
groups/permissions and keeps changes made in the admin;
`setup_roles --reset` restores the defaults exactly. Users of the Admin role
also need "Mitarbeiter-Status" (is_staff) to open `/admin/`.

## Switching to PostgreSQL

Set `DATABASE_ENGINE=postgres` and the `POSTGRES_*` values in `.env`, then
run `python manage.py migrate` and `python manage.py setup_roles` again.
