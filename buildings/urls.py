from django.urls import path

from . import views

app_name = "buildings"

urlpatterns = [
    path("", views.building_list, name="list"),
    path("zeilen/", views.building_rows, name="rows"),
    path("<int:pk>/zeile/", views.building_row, name="row"),
]
