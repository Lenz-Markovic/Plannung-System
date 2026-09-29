from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("", views.receipt_list, name="list"),
    path("<int:pk>/speichern/", views.receipt_update, name="update"),
    path("warnung/", views.deadline_warning, name="warning"),
    path("aushang/drucken/", views.notice_print, name="notice_print"),
    path("aushang/", views.notice_page, name="notice_page"),
    path("warnung/spaeter/", views.warning_snooze, name="warning_snooze"),
    path("warnung/<int:pk>/status/", views.warning_status, name="warning_status"),
    path("warnung/<int:pk>/ansehen/", views.warning_show_in_list, name="warning_show"),
]
