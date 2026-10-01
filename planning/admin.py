from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Absence, Employee, Tour, TourStop


@admin.register(Employee)
class EmployeeAdmin(SimpleHistoryAdmin):
    list_display = ["short_name", "user", "can_read", "can_install", "can_notice", "city", "active"]
    list_filter = ["can_read", "can_install", "can_notice", "active"]
    search_fields = ["short_name", "user__username"]


@admin.register(Absence)
class AbsenceAdmin(SimpleHistoryAdmin):
    list_display = ["employee", "kind", "start_date", "end_date"]
    list_filter = ["kind", "employee"]


class TourStopInline(admin.TabularInline):
    model = TourStop
    fk_name = "tour"  # the stops of THIS plan (help_tour points to another plan)
    extra = 0
    fields = ["position", "kind", "building", "installation_order", "help_tour", "is_fixed", "start_time", "end_time", "work_minutes", "drive_to_next_minutes", "done_at"]
    raw_id_fields = ["building", "installation_order", "help_tour"]


@admin.register(Tour)
class TourAdmin(SimpleHistoryAdmin):
    list_display = ["date", "employee", "status", "routing_source", "net_minutes", "needs_recalculation", "version"]
    list_filter = ["status", "routing_source", "employee"]
    date_hierarchy = "date"
    readonly_fields = ["version", "created_at", "updated_at"]
    inlines = [TourStopInline]


@admin.register(TourStop)
class TourStopAdmin(SimpleHistoryAdmin):
    list_display = ["tour", "position", "kind", "building", "installation_order", "done_at"]
    list_filter = ["kind"]
    raw_id_fields = ["tour", "building", "installation_order"]
