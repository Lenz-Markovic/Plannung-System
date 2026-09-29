# SimpleHistoryAdmin adds a "Historie" button with who/when/old/new value.
from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import ArticlePrice, Building, DeviceCategory, InstallationOrder, InstallationOrderItem, PropertyManager


@admin.register(PropertyManager)
class PropertyManagerAdmin(SimpleHistoryAdmin):
    search_fields = ["name"]


@admin.register(Building)
class BuildingAdmin(SimpleHistoryAdmin):
    list_display = ["file_number", "source_system", "street", "zip_code", "city", "region", "status", "assigned_reader", "stichtag"]
    list_filter = ["status", "source_system", "region", "stichtag", "installation_type"]
    search_fields = ["file_number", "file_number_core", "street", "city", "zip_code"]
    autocomplete_fields = ["property_manager"]
    readonly_fields = ["file_number_core", "created_at", "updated_at"]


class InstallationOrderItemInline(admin.TabularInline):
    model = InstallationOrderItem
    extra = 0


@admin.register(InstallationOrder)
class InstallationOrderAdmin(SimpleHistoryAdmin):
    list_display = ["re_number", "street", "city", "building", "status", "priority", "duration_minutes"]
    list_filter = ["status", "priority"]
    search_fields = ["re_number", "process_number", "building_file_number", "street", "city"]
    raw_id_fields = ["building"]
    filter_horizontal = ["assigned_installers"]
    readonly_fields = ["building_file_number_core", "created_at", "updated_at"]
    inlines = [InstallationOrderItemInline]


@admin.register(DeviceCategory)
class DeviceCategoryAdmin(admin.ModelAdmin):
    list_display = ["code", "label", "minutes_per_piece", "price"]
    list_editable = ["minutes_per_piece", "price"]


@admin.register(ArticlePrice)
class ArticlePriceAdmin(admin.ModelAdmin):
    list_display = ["article_number", "description", "price"]
    list_editable = ["price"]
    search_fields = ["article_number", "description"]
