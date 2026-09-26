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
```

## Daily use

```bash
source .venv/bin/activate
python manage.py runserver           # http://127.0.0.1:8000
pytest                               # run all tests
```

After changing a `models.py`: `python manage.py makemigrations` and then
`python manage.py migrate`.

## Project layout

| Folder | Contents |
|---|---|
| `config/` | settings (read from `.env`), URLs |
| `core/` | shared base models, roles (`core/roles.py`), login, start page, user admin |
| `buildings/` | buildings, property managers, installation orders (+ import later) |
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
