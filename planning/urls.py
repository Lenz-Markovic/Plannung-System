from django.urls import path

from . import views

app_name = "planning"

urlpatterns = [
    path("auswahl/", views.select, name="select"),
    path("auswahl/leeren/", views.select_clear, name="select_clear"),
    path("neu/", views.plan_dialog, name="dialog"),
    path("entwurf/", views.draft, name="draft"),
    path("entwurf/aktion/", views.draft_action, name="draft_action"),
    path("entwurf/speichern/", views.draft_save, name="draft_save"),
    path("entwurf/verwerfen/", views.draft_discard, name="draft_discard"),
]
