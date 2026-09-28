from django.contrib import admin
from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path("", core_views.home, name="home"),
    # login/, logout/, password_change/ ... (templates in templates/registration/)
    path("liegenschaften/", include("buildings.urls")),
    path("unterlagen/", include("documents.urls")),
    path("konto/", include("django.contrib.auth.urls")),
    path("admin/", admin.site.urls),
]
