from django.contrib import admin

from .models import Discount


@admin.register(Discount)
class DiscountAdmin(admin.ModelAdmin):
    list_display = ("code", "percent_off", "is_enabled", "created_at")
    list_filter = ("is_enabled",)
    search_fields = ("code",)
    readonly_fields = ("uuid", "created_at", "updated_at")
