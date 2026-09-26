from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import CostDocumentReceipt, CoverSheet


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
