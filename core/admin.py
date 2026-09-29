"""
User administration with the Employee data shown on the same page.

"Benutzer und Rollen verwalten": users, their role (group) and - for readers
and installers - their employee data (home address, colour, time window).
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.shortcuts import redirect

from planning.models import Employee

from .models import Features


class EmployeeInline(admin.StackedInline):
    model = Employee
    can_delete = False
    verbose_name_plural = "Mitarbeiterdaten (nur für Ableser/Monteure)"
    fields = [
        "short_name", "can_read", "can_install", "active", "calendar_color",
        "max_daily_minutes", "default_start_time", "work_window_start", "work_window_end",
        "street", "zip_code", "city",
    ]


class UserWithEmployeeAdmin(UserAdmin):
    inlines = [EmployeeInline]
    list_display = ["username", "first_name", "last_name", "role_names", "is_active"]
    list_filter = ["groups", "is_active"]

    @admin.display(description="Rollen")
    def role_names(self, user):
        return ", ".join(group.name for group in user.groups.all())


admin.site.unregister(User)
admin.site.register(User, UserWithEmployeeAdmin)

admin.site.site_header = "Planungssystem – Verwaltung"
admin.site.site_title = "Planungssystem"


@admin.register(Features)
class FeaturesAdmin(admin.ModelAdmin):
    """Only one row: no "add" (except the very first time) and no "delete"."""

    def has_add_permission(self, request):
        return not Features.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # straight to the one row instead of a list with one entry
        return redirect("admin:core_features_change", Features.load().pk)
