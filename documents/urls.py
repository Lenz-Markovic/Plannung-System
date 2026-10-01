from django.urls import path

from . import aushang_views, views

app_name = "documents"

urlpatterns = [
    path("", views.receipt_list, name="list"),
    path("<int:pk>/speichern/", views.receipt_update, name="update"),
    path("warnung/", views.deadline_warning, name="warning"),
    path("aushang/drucken/", views.notice_print, name="notice_print"),
    path("aushang/", views.notice_page, name="notice_page"),
    path("aushang/auswahl/", views.notice_toggle, name="notice_toggle"),
    path("aushang/sicher/", views.notice_confirm, name="notice_confirm"),
    path("aushaenge/", aushang_views.aushaenge_page, name="aushaenge"),
    path("aushaenge/<int:pk>/aushang/", aushang_views.aushang_wanted, name="aushang_wanted"),
    path("aushaenge/<int:pk>/aendern/", aushang_views.aushang_edit, name="aushang_edit"),
    path("aushaenge/<int:pk>/ankuendigung/", aushang_views.aushang_choice, name="aushang_choice"),
    path("aushaenge/<int:pk>/zugang/", aushang_views.aushang_access, name="aushang_access"),
    path("aushaenge/route/", aushang_views.aushang_route, name="aushang_route"),
    path("warnung/spaeter/", views.warning_snooze, name="warning_snooze"),
    path("warnung/<int:pk>/status/", views.warning_status, name="warning_status"),
    path("warnung/<int:pk>/ansehen/", views.warning_show_in_list, name="warning_show"),
]
