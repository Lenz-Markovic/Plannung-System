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

`pytest` takes about a minute. Tests that need the demo data ask for the
`demo_import` fixture (`conftest.py`): the data is imported once per test file
and every test is rolled back afterwards, so each test starts with the same
fresh data. Only one file: `pytest planning/tests/test_excel.py`.

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

## Access detection and Excel export

- `buildings/rules/access.py` (port of `zugangErkennen`, same result as the
  prototype for all 240 demo buildings): 🔑 apartment access / 🚪 boiler room,
  unit numbers (NE003), key and announcement hints. Shown in the list,
  the plan preview, the calendar and the Excel file; recalculated when a
  note changes.
- `planning/excel.py`: Excel in the prototype's layout (same widths, fonts,
  colours, borders, row heights, page setup): overview "Fahrpläne", one
  sheet per person, one A4 print sheet per tour. Downloaded automatically
  after saving a plan; also "📄 Excel" in the calendar side panel and
  "📄 Alle Fahrpläne (Excel)" in the calendar.

## Mobile day plan ("Mein Tag") and near-live updates

- Readers and installers land on **📱 Mein Tag** after login (`/planung/mein-tag/`):
  one card per stop, tap the address to start the navigation, phone numbers are
  clickable, "✓ Stopp erledigt", a note on site and a status *suggestion*.
  When all stops are done the tour becomes "erledigt".
- Readers only see and change their OWN stops. Office roles can open any person's day
  with the person selector.
- If the office changes the tour, the reader's page reloads itself within a minute.
- The office sees the suggestion in the building list ("Vorschlag: …") and takes it
  over with one click on **✓ übernehmen**.
- The building list checks every 20 s for rows changed by colleagues (also stops ticked
  off by readers), replaces them and shows who changed them. The calendar reloads its
  tours every 60 s.
- Try it: `python manage.py demo_day` gives the demo reader a plan for today, then log
  in as `ableser_demo`.

## TomTom map

- The plan preview, the calendar side panel and "Mein Tag" (🗺 Karte des Tages) show
  a real TomTom map: green = start, blue = stops, red = end. In the preview the line
  follows the roads calculated by TomTom; click a stop number to show it on the map.
- **The key stays on the server:** the browser loads the map images from our own
  address `/planung/karte/<z>/<x>/<y>.png` (login needed). Django fetches the image
  from TomTom with the key, keeps it for a week in the cache and passes it on.
  The TomTom SDK in the browser only gets the placeholder `server-side`.
- In the TomTom developer portal the key needs the **Map Display API** as well
  (next to Search and Routing). Without a key the simple sketch is shown instead.

## Montageaufträge and conflicts

- **🔧 Montage** (`/montage/`): the order list of the Montage dashboard, without prices.
  Filters (search, priority, installer, status, appointment, conflicts, order date),
  clickable tiles, ▸ opens positions, appointments and messages. Time, installers
  (max. 3), priority and status save themselves.
- Tick orders → **🔧 Montage planen** → installer, day, start → the same preview as for
  readings. The preview checks each order against the planned readings of its building.
  Saved orders become "Verplant", and go back to "Offen" when their plan is deleted.
- **⚠ Konflikte** (`/konflikte/`): all messages of `bewerteMontage()` from the prototype
  (installation after / on the reading day, less than 7 days between them, radio
  retrofit with a manual reading type, installer = reader, gateway not marked, no
  appointment yet). A dispatcher can accept a conflict with a reason
  (**bewusst übernehmen**). The red number in the navigation counts the open ones.
- The check is a pure function (`conflicts/rules.py`, verified against the prototype
  for all 240 demo buildings). `conflicts/services.py` stores the result after every
  planning change. After an update run `python manage.py update_conflicts` once.

## One plan with readings and installations, clearer calendar

- Tick buildings in 🏢 Liegenschaften **and** orders in 🔧 Montage: both buttons show
  the other count ("+ 🔧 2") and open the same dialog → one plan with both.
- In the preview: **＋ Stopp hinzufügen** (search AZ, RE-Nr., street, town) and
  "Passt dazu" suggestions (open order for a building in the plan, the reading of a
  building that gets an installation, orders assigned to this installer). A warning
  appears if the person is not registered as Ableser / Monteur for a stop.
- Everywhere: 📖 Ablesung = blue, 🔧 Montage = amber and striped, 📖🔧 = both in one plan.
- Calendar: click a **person** → only their plans + overview (workload of 3 weeks, next
  plans, assigned orders without date, absences); click again → everybody. Buttons
  alle / Ablesung / Montage / beides filter the plans. Mouse over a plan shows its
  stops. Click a **day** → who is planned, absent, still free, with "planen" (dialog
  with day and person filled in).
- The ⚠ in the calendar now comes from the stored conflicts: accepted ones disappear.

## Creating a plan: the last question "Bist du sicher?"

- "Fahrplan prüfen" has ONE button **🗺 Fahrplan erstellen …**. It always opens the
  question "Fahrplan wirklich so erstellen?" with a summary (person, day, stops,
  times, net working time, TomTom, conflicts). Only there **✓ Fahrplan final erstellen**
  (confirmed, needs TomTom times) or **vorläufig erstellen** really saves the plan; the
  server refuses to save without this answer.
- **Over 7,5 h / under 6 h:** the question pops up by itself as soon as the plan opens,
  with suggestions that make sense:
  - too long: **⇄** swap a stop against an unplanned stop nearby that fits into the
    time (the stop whose removal is enough, smallest first), or **✕ nur herausnehmen**;
  - too short: unplanned stops nearby (readings and installations the person can do)
    that still fit, **＋ dazu**.
- **Selbst eingeben** (in the same question): type an RE number, AZ, street or town.
  Every result shows its minutes, the drive from the nearest stop, whether it still
  fits into the day (✓ / ⚠) and whether it is already planned elsewhere; buttons
  **＋ dazu** and, for a day that is too long, **⇄ statt …** where swapping makes it fit.
- Automatic choices of the system keep to 7,5 h strictly (`fits_in_day`).
- CSS/JS links carry `?v=<version>` (`core/context_processors.py`), so browsers load the
  new files after an update instead of old copies from their cache.

## Teams, first free day, weekly hours

- **👥 Team** (big objects): in "Fahrplan prüfen" add up to 3 more people to a plan.
  Their day counts as planned, the work time per stop is split on the team (rounded
  up to 5 min, can be switched off), team members see the plan in "Mein Tag" and may
  tick stops. Calendar, side panel and Excel show "Keller + Kaiser". A team member who
  is absent or already planned that day is refused (also for "vorläufig").
- **🗓 Erste freie Tage** (calendar button, on/off): a green marker "🟢 frei: Kaiser" on
  the first free working day (Mon–Fri, no plan, not in a team, not absent) of every
  person, plus the list on the right. Works with the filters (📖 only readers, 🔧 only
  installers). Click a marker → planning panel for that person and day with the
  system's suggestions (assigned buildings / orders first, then the usual region, close
  together); the ones that fit into 7,5 h are ticked → "Fahrplan erstellen" opens the
  preview. Also in the person overview.
- **Weekly hours** in the person overview only with the permission "Wochenstunden je
  Mitarbeiter sehen" (only Admin by default, changeable in the admin). After updating
  run `python manage.py setup_roles` once so the Admin role gets it. Daily hours stay
  visible for everybody.

## 🤝 Help at one object

- A **team** (👥) is fixed for the whole day: its members cannot be planned anywhere
  else that day (the team list only offers people who are still free).
- **Help at ONE object:** in the calendar side panel every stop has **🤝 + Helfer**.
  Choose a person → their day opens in "Fahrplan prüfen" with a stop
  "🤝 Hilfe bei Keller" (in their own plan, or a new plan if they have none). The
  work time at that object is shared (e.g. 350 min, 2 people → 175 min each). A warning
  appears if the helper would come when the others are not there.
- After saving, the helped plan is marked "⟳ neu rechnen"; recalculated, it shows
  "🤝 Helfer: Becker 10:30–13:25" and its own time at the object is shared, too.
  Calendar ("🤝 1× Hilfe"), Excel and "Mein Tag" show the help stop. Deleting one of
  the plans marks the other one for recalculation.

## 🤖 Automatic planning (FROZEN – switched off)

> Decided with the team: this function is frozen for now and will be developed further
> later. It is **switched off** in the real system: no button, the page cannot be opened.
> An admin can switch it on in **⚙ Verwaltung → Funktionen → „🤖 Automatisch planen“**
> (model `core.Features`, one row). The code and its tests stay.

- Calendar → **🤖 Automatisch planen**: choose the period (max. one month), the kind
  (📖 / 🔧 / both) and the people (none ticked = all) → **Vorschlag berechnen**.
- The system (pure rule `planning/rules/autoplan.py`) spreads every unplanned stop
  over the free working days (no plan, not in a team, not absent): never over 7,5 h,
  never more than 1 h drive between two stops, always the nearest next stop; assigned
  stops go to their person first, the most urgent first. Montage rule: an installation
  at least 8 days before the reading of its building - also inside one run (the reading
  waits for its installation). Released buildings and done orders are not planned.
- The proposal page shows one card per person and day (estimated hours). Remove single
  stops (✕) or whole days, or open a day in "Fahrplan prüfen" (exact TomTom times).
  **Alle … vorläufig erstellen** (after "Bist du sicher?") saves all days as provisional
  plans - confirm them one by one in the calendar as usual.

## 📝 Notizen (Terminierung ↔ Planung)

- Notes on a **Liegenschaft** or a **Montageauftrag**, kinds: 📝 Hinweis, ⛔ **Storno**,
  📅 Terminwunsch, 🔑 Zugang / Schlüssel. Written by the Terminierung (role
  Sachbearbeitung) and the Disposition; Leitung and Admin can read them (Admin all).
- Where: in the detail row of the Liegenschaften and Montage lists (▸), as a badge
  in the row (📝 n / ⛔ Storno - click opens them), at every stop in **Fahrplan prüfen**
  and in the **calendar side panel** ("📝 Notizen · ＋ neue" opens a pop-up).
- **Add more on top**: every note keeps its author and time; new ones come on top.
  "✓ erledigt" ticks a note off (it stays, greyed), "↺ wieder offen" reopens it.
- An **open ⛔ Storno** keeps the object out of every suggestion (plan suggestions,
  free days, "passt noch in den Tag", automatic planning). The search still finds it,
  marked "⛔ Storno gemeldet", and "Bist du sicher?" asks: "✕ aus dem Plan nehmen" or
  plan it anyway.
- A new note shows up at once in an open plan / side panel (`notes-changed`).

## 🧾 After the visit: Ergebnis, Nachtermin, Bearbeitung

- In **Mein Tag** every stop gets an Ergebnis (`planning/rules/visits.py`):
  **✓ fertig (100 %)** · **◐ teilweise erledigt** (must write what is still to do, e.g.
  "NE003 und NE007 fehlen") · **✗ niemand da / nicht möglich** (reason: niemand angetroffen,
  kein Zugang, abgelehnt, Gerät defekt, Sonstiges + what to do next). "↺ Ergebnis ändern"
  takes it back.
- Every report is kept as a **Termin-Ergebnis** (`planning.Visit`): 1. Termin, 2. Termin
  (Nachtermin), 3. Termin … with day, people, Ergebnis, what is still to do, note on site,
  who reported it. It stays even when a plan is changed or deleted; a visited stop stays in
  its old plan as history (planning the object again does not take it out of there).
- **🔁 Nachtermin nötig** = last visit teilweise / nicht erledigt, not closed by the office
  and not planned again yet: tile + filter in Liegenschaften ("Nachtermin") and Montage
  ("Termin → Nachtermin nötig"), badge in the row; such objects are suggested again
  (free days, "passt noch in den Tag").
- Planning it again: Fahrplan prüfen, the calendar panel and the Excel Fahrplan show
  **"🔁 2. Termin (Nachtermin) – zuletzt 05.10. Keller: ◐ teilweise · noch zu tun: …"**.
- **🧾 Bearbeitung** in the detail row (▸) of every Liegenschaft and Montageauftrag: all
  visits with their Ergebnis, the next planned appointment (which Termin it will be), and
  "✓ abschließen – kein Nachtermin nötig" for the office (e.g. settled on the phone).
- Mein Tag on a PC is the normal page again (no phone frame).

## 📱 Reports from "Mein Tag" (live in the office)

- On the phone every stop has "Notiz vor Ort" and **⚠ Problem ans Büro melden** (the note
  goes to the office as a ⚠ note on the building / order; empty = "Bitte kurz beschreiben").
- The calendar side panel of the plan shows per stop: ✓ erledigt (time, person), 📱 vor
  Ort: the note, ⚠ Problem vor Ort, 🏷 status proposal of the reader. It checks every
  30 s and reloads ONLY if something changed (nothing you type is lost otherwise).
- Lists: badge **⚠ Problem**; Verlauf: filter **📱 vor Ort**.
- **Mein Tag** itself is only for Ableser/Monteur, Admin and roles that get "Eigenen
  Tagesplan sehen" in ⚙ Verwaltung → Gruppen (no longer for Disposition by default).

## 🕘 Verlauf (who changed what)

- The Verlauf starts with everything from before the update (taken over once from the
  change history: plans, printed notices, status changes); since then every step is
  recorded with the person.
- Purpose: to know **whom to ask** about a step. So it is kept out of the way: click
  your **name in the navigation (▾) → 🕘 Verlauf – wer hat was geändert?**. It opens a side drawer: newest first, grouped by day,
  with who and when. Filters: Fahrpläne, Aushänge, Aufträge, Status, Notizen,
  Unterlagen; "nur meine"; last 24 h / 7 / 30 days. Refreshes itself every 30 s.
- Recorded (`journal/activity.py`): plan created / changed / **confirmed** / **Termin
  verschoben** (old → new person and day) / deleted, stop done (Mein Tag), **Aushang
  gedruckt / als Word geladen / ja-nein**, order priority / status / Montagezeit /
  Monteure, building status and status proposals, Unterlagen-Eingang, notes.
- Quietly where it matters: the calendar side panel ends with a small grey line
  "erstellt von … · bestätigt von … · zuletzt: … – <person>, <time> · wer hat was
  geändert?"; a printed notice says "✓ Aushang gedruckt 29.09. von <person>"; every
  note shows its author. The notes box has "wer hat was geändert?" for its object.
- Ableser/Monteur see no Aushang information and no Verlauf on their side (calendar
  panel of their own plan, Mein Tag).
- **Who may see it**: permission `journal.view_activity` - Admin, Disposition,
  Sachbearbeitung, Leitung by default; not Ableser/Monteur. Change it per role in
  ⚙ Verwaltung → Gruppen. The Verlauf itself cannot be edited or deleted (admin read only).
- After an update `python manage.py migrate` is enough: NEW default permissions reach the
  roles by themselves (core/role_sync.py). A permission an admin took away stays away.

## 💶 Material & Kosten (Montage, Admin only)

- **🔧 Montage → 💶 Material & Kosten** (permission `buildings.view_costs`, only the
  Admin role by default): which material and how much money the installations of a
  time window need - to plan the budget and order in time.
- Time window from today: **1 Woche, 2 Wochen, 1 Monat, 3 Monate**, or **von – bis**
  with your own dates. The URL keeps the window (bookmark it).
- Counted orders (`buildings/rules/material.py`, pure functions):
  - 📅 **geplant im Zeitraum** - the installation day of its Fahrplan is in the window
  - ⏳ **noch ungeplant, aber bis dahin fällig** - latest day (8 days before the
    building's reading) is before the end of the window, also when already overdue
  - ❔ **offen ohne Frist** - not in the total; switch "auch offene … dazurechnen" on
  - done orders without a plan are not counted (nothing to buy any more).
- **Bestellliste** per article: pieces per group, price per piece, sum, "zuerst
  gebraucht" (first day it is needed) and which orders (click "n Auftr.").
  **⬇ Excel-Bestellliste** downloads the same list plus a sheet "Budget je Woche".
- Tab **📋 Je Auftrag**: one row per contract (RE) - day, address, pieces of every
  material category, total pieces and money; click the address for its articles with
  prices, the RE number opens the order in the Montage dashboard. The Excel file has
  the same table on the sheet "Je Auftrag".
- Budget per calendar week and per category as bars.
- **Prices**: per category (right side, the prototype prices as start values) and a fixed
  price per article number directly in the table (empty = category price again). A
  price change updates all sums at once. Also in ⚙ Verwaltung → Gerätekategorien /
  Artikelpreise. Articles without a price are marked (the sum would be too low).
- After the update run `python manage.py setup_roles` once, so the Admin role gets the
  new permission.

## 📄 Tenant notices (Aushang)

- **Optional per stop** - not every building gets a notice. In the calendar side panel
  of a plan every stop shows "📄 kein Aushang · **＋ Aushang**"; switch it on only where
  needed ("＋ für alle …" switches on all at once, "✕ kein Aushang" off again). Only
  switched-on stops count for the deadline warning. The choice stays when the plan is
  saved again or moved.
- The page is the **company template** `documents/vorlage/Aushang_Vorlage.dotx`
  ("VorlageAushänge", unchanged). Filled automatically (`documents/aushang_fields.py`):
  - top left: **Liegenschaftsnummer** (AZ; for an order without building its number/RE)
  - "in der Liegenschaft": street, ZIP, city
  - boxes: Ablesung (+ Wartung/Sichtkontrolle and Rauchwarnmelder when the smoke
    detectors are checked) or Montage / Austausch (order type); devices from the
    building counts (HKV, Wasser, Wärme) or from the order items (EHKV, SQ1, WMZ, RWM)
  - **am:** weekday (the template's list Montag–Samstag), **dem:** date,
    **zwischen / ab:** time window, e.g. "08:00 – 10:00 Uhr" (full hour before the
    start, at least 2 h - `documents/notice_rules.py`)
  - the free field at the bottom stays empty.
- **🖨 Aushänge drucken (n)** opens the pages in the browser: the template picture with
  the fields at exactly the places of the Word form fields. Texts and boxes can be
  clicked and changed before printing (only for that print). Print, or "Als PDF
  speichern" in the print dialog.
- **⬇ Word (n)** downloads ONE .docx with one page per notice: the template itself with
  its form fields filled (standard library only, `documents/aushang_docx.py`) - to
  change it further in Word.
- Print **any time**: also "🖨 gleich drucken" at a stop without "＋ Aushang". When it is
  already too late (less than 14 days before, or the day is over), a question comes first -
  like "Fahrplan erstellen": "Aushang ist schon zu spät – trotzdem drucken?".
- Ableser/Monteur see no Aushang information on their side.
- Printing / Word marks the stops "✓ Aushang gedruckt … von <person>"; the panel shows the deadline
  (14 days before, red when late) and **⚠ veraltet** if the plan was moved or its time
  window changed since.
- A new version of the template: replace `documents/vorlage/Aushang_Vorlage.dotx` (same
  field names) and `static/img/aushang_vorlage.jpg` (its page picture, `word/media/image1.jpeg`).

## Project layout

| Folder | Contents |
|---|---|
| `config/` | settings (read from `.env`), URLs |
| `core/` | shared base models, roles (`core/roles.py`), login, start page, user admin |
| `buildings/` | buildings, property managers, installation orders, prototype import (`importers/`) |
| `planning/` | employees, absences, tours and stops (+ TomTom client, working-time rules later) |
| `conflicts/` | stored conflict results (+ rules later) |
| `documents/` | cover sheets, received cost documents / 14-day deadline |
| `journal/` | 📝 notes (Storno …) on buildings and orders, 🕘 Verlauf (who changed what) |
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
