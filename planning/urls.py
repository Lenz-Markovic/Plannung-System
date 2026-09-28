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
    path("kalender/", views.calendar_page, name="calendar"),
    path("kalender/termine/", views.calendar_feed, name="calendar_feed"),
    path("fahrplan/<int:pk>/", views.tour_detail, name="tour_detail"),
    path("fahrplan/<int:pk>/verschieben/", views.tour_move, name="tour_move"),
    path("fahrplan/<int:pk>/neu-rechnen/", views.tour_recalculate, name="tour_recalculate"),
    path("fahrplan/<int:pk>/loeschen/", views.tour_delete, name="tour_delete"),
    path("fahrplan/<int:pk>/excel/", views.tour_excel, name="tour_excel"),
    path("excel/", views.tours_excel, name="tours_excel"),
    path("mein-tag/", views.my_day, name="my_day"),
    path("mein-tag/pruefen/<int:pk>/", views.my_day_check, name="my_day_check"),
    path("stopp/<int:pk>/erledigt/", views.stop_done, name="stop_done"),
    path("stopp/<int:pk>/notiz/", views.stop_note, name="stop_note"),
    path("stopp/<int:pk>/vorschlag/", views.stop_propose, name="stop_propose"),
]
