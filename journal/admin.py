from django.contrib import admin

from .models import Activity, Note


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ["created_at", "kind", "text", "building", "installation_order", "author", "resolved_at"]
    list_filter = ["kind", ("resolved_at", admin.EmptyFieldListFilter)]
    search_fields = ["text", "building__file_number", "installation_order__re_number"]
    raw_id_fields = ["building", "installation_order"]


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    """The Verlauf is a record: read only."""

    list_display = ["created_at", "user", "kind", "text"]
    list_filter = ["kind", "user"]
    search_fields = ["text"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
