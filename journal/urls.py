from django.urls import path

from . import views

app_name = "journal"

urlpatterns = [
    path("notizen/<str:target>/<int:pk>/", views.notes_box, name="notes"),
    path("notizen/<str:target>/<int:pk>/neu/", views.note_add, name="note_add"),
    path("notizen/<int:pk>/erledigt/", views.note_resolve, name="note_resolve"),
    path("verlauf/", views.activity_drawer, name="activity"),
]
