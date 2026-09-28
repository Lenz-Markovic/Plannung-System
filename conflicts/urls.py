from django.urls import path

from . import views

app_name = "conflicts"

urlpatterns = [
    path("", views.conflict_list, name="list"),
    path("zaehler/", views.conflict_badge, name="badge"),
    path("<int:pk>/uebernehmen/", views.conflict_acknowledge, name="acknowledge"),
]
