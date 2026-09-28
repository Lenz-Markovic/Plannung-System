from django.urls import path

from . import views

app_name = "buildings"

urlpatterns = [
    path("", views.building_list, name="list"),
    path("zeilen/", views.building_rows, name="rows"),
    path("aenderungen/", views.building_changes, name="changes"),
    path("<int:pk>/zeile/", views.building_row, name="row"),
    path("<int:pk>/speichern/", views.building_update, name="update"),
]
