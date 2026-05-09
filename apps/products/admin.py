from django.contrib import admin
from django.utils.html import format_html

from .models import Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "price_eur_display",
        "stock_quantity",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("uuid", "image_preview", "created_at", "updated_at")
    fieldsets = (
        ("Product", {
            "fields": ("uuid", "name", "slug", "description"),
        }),
        ("Catalog", {
            "fields": ("price_cents", "stock_quantity", "is_active"),
        }),
        ("Image", {
            "fields": ("image", "image_preview"),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
        }),
    )

    @admin.display(description="Price")
    def price_eur_display(self, obj):
        return f"{obj.price_cents / 100:.2f} €"

    @admin.display(description="Preview")
    def image_preview(self, obj):
        if not obj.image:
            return "—"
        return format_html(
            '<img src="{}" style="max-height:160px;max-width:240px;border-radius:6px;" />',
            obj.image.url,
        )
