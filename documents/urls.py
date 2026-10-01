from django.urls import path

from . import announce_views, views

app_name = "documents"

urlpatterns = [
    path("", views.receipt_list, name="list"),
    path("<int:pk>/speichern/", views.receipt_update, name="update"),
    path("warnung/", views.deadline_warning, name="warning"),
    path("aushang/drucken/", views.notice_print, name="notice_print"),
    path("aushang/", views.notice_page, name="notice_page"),
    path("aushang/auswahl/", views.notice_toggle, name="notice_toggle"),
    path("aushang/sicher/", views.notice_confirm, name="notice_confirm"),
    path("ankuendigung/", announce_views.announce_page, name="announce"),
    path("ankuendigung/<int:pk>/speichern/", announce_views.announce_save, name="announce_save"),
    path("ankuendigung/<int:pk>/erledigt/", announce_views.announce_sent, name="announce_sent"),
    path("ankuendigung/fahrt/auswahl/", announce_views.notice_select, name="notice_select"),
    path("ankuendigung/fahrt/leeren/", announce_views.notice_select_clear, name="notice_select_clear"),
    path("warnung/spaeter/", views.warning_snooze, name="warning_snooze"),
    path("warnung/<int:pk>/status/", views.warning_status, name="warning_status"),
    path("warnung/<int:pk>/ansehen/", views.warning_show_in_list, name="warning_show"),
]
