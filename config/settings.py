"""
Django settings for the planning system.

All secrets and environment-specific values come from environment variables.
For local development they are read from the file ".env" in the project root
(see ".env.example"). Never put secrets directly into this file.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load ".env" into os.environ (existing environment variables win).
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --- Security -----------------------------------------------------------------

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
# Needed when the site is opened via another address, e.g. GitHub Codespaces:
# DJANGO_CSRF_TRUSTED_ORIGINS=https://*.app.github.dev
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "insecure-dev-key-only-for-local-debugging"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY is not set (see .env.example).")

# TomTom: used only by our backend (planning/tomtom.py). Never pass this
# value to a template or to JavaScript.
# strip(): removes spaces, line breaks and quotes that easily sneak in when copying.
TOMTOM_API_KEY = os.environ.get("TOMTOM_API_KEY", "").strip().strip("\"'").strip()

# Red stripe "DEMO - alle Daten frei erfunden" at the top (as in the prototype)
DEMO_BANNER = env_bool("DEMO_BANNER", True)


# --- Applications -------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",  # number formatting (9.734)
    # third party
    "django_filters",
    "simple_history",
    # our apps
    "core",
    "buildings",
    "planning",
    "conflicts",
    "documents",
    "journal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Stores the logged-in user in every history entry ("who changed it").
    "simple_history.middleware.HistoryRequestMiddleware",
    # request.htmx / request.htmx_target (see core/middleware.py)
    "core.middleware.HtmxMiddleware",
    # "Datenbank nicht aktuell - bitte migrate" instead of a crash after an update
    "core.middleware.DatabaseNotUpToDateMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# --- Database -----------------------------------------------------------------
# SQLite during the first week, PostgreSQL later: switch with DATABASE_ENGINE.

if os.environ.get("DATABASE_ENGINE", "sqlite") == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "planung"),
            "USER": os.environ.get("POSTGRES_USER", "planung"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Login --------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"


# --- Language and time --------------------------------------------------------

LANGUAGE_CODE = "de-de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True


# --- Static files -------------------------------------------------------------

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
