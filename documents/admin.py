from django.contrib import admin
from django.shortcuts import redirect
from simple_history.admin import SimpleHistoryAdmin

from .models import CostDocumentReceipt, CoverSheet, NoticeSettings


@admin.register(CoverSheet)
class CoverSheetAdmin(admin.ModelAdmin):
    list_display = ["building", "source_file", "page", "property_manager_text"]
    search_fields = ["building__file_number", "property_manager_text", "owner_text"]
    raw_id_fields = ["building"]


@admin.register(CostDocumentReceipt)
class CostDocumentReceiptAdmin(SimpleHistoryAdmin):
    list_display = ["building", "received_on", "deadline_start", "reset_reason"]
    date_hierarchy = "received_on"
    raw_id_fields = ["building"]


@admin.register(NoticeSettings)
class NoticeSettingsAdmin(admin.ModelAdmin):
    """Only one row: no list, no delete."""

    def has_add_permission(self, request):
        return not NoticeSettings.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        return redirect("admin:documents_noticesettings_change", NoticeSettings.load().pk)
