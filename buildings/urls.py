from django.urls import path

from . import gateway_views, views

app_name = "buildings"

urlpatterns = [
    path("", views.building_list, name="list"),
    path("zeilen/", views.building_rows, name="rows"),
    path("aenderungen/", views.building_changes, name="changes"),
    path("<int:pk>/zeile/", views.building_row, name="row"),
    path("<int:pk>/speichern/", views.building_update, name="update"),
    path("gateways/", gateway_views.gateway_list, name="gateways"),
    path("<int:pk>/gateway/", gateway_views.gateway_save, name="gateway_save"),
    path("<int:pk>/gateway/freigeben/", gateway_views.gateway_release, name="gateway_release"),
]
