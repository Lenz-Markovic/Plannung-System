from django.contrib import admin

from .models import Conflict


@admin.register(Conflict)
class ConflictAdmin(admin.ModelAdmin):
    list_display = ["severity", "rule", "building", "installation_order", "created_at", "acknowledged_by"]
    list_filter = ["severity", "rule"]
    raw_id_fields = ["building", "installation_order", "stop", "other_stop"]
