from django.contrib import admin
from django.utils.html import format_html

from .models import Order, OrderFile


class OrderFileInline(admin.TabularInline):
    model = OrderFile
    extra = 0
    fields = ("kind", "file", "mime_type", "size_bytes", "created_at")
    readonly_fields = ("mime_type", "size_bytes", "created_at")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "short_pk",
        "kind",
        "status",
        "material_or_product",
        "size_display",
        "quantity_display",
        "total_eur",
        "created_by",
        "created_at",
    )
    list_filter = (
        "kind",
        "status",
        "material",
        "with_relief",
        "with_tinta_blanca",
        "with_barniz_brillo",
        "with_barniz_opaco",
    )
    search_fields = (
        "uuid",
        "recipient_name",
        "city",
        "postal_code",
        "stripe_payment_intent_id",
        "created_by__email",
        "product__name",
    )
    ordering = ("-created_at",)
    date_hierarchy = "created_at"

    _STICKER_FIELDSET = ("Sticker spec", {
        "fields": (
            "material",
            ("width_mm", "height_mm"),
            "quantity",
            "with_relief",
            "with_tinta_blanca",
            ("with_barniz_brillo", "with_barniz_opaco"),
            "relief_note",
        ),
    })
    _CATALOG_FIELDSET = ("Catalog item", {
        "fields": ("product", "product_quantity"),
    })
    _COMMON_FIELDSETS = (
        ("Order", {
            "fields": ("uuid", "kind", "status", "created_by"),
        }),
    )
    _TRAILING_FIELDSETS = (
        ("Shipping", {
            "fields": (
                "recipient_name",
                "street_line_1",
                "street_line_2",
                ("city", "postal_code", "country"),
            ),
        }),
        ("Money", {
            "fields": ("total_amount_cents", "currency", "stripe_payment_intent_id"),
        }),
        ("Lifecycle", {
            "fields": (
                "placed_at",
                "paid_at",
                "shipped_at",
                "delivered_at",
                "cancelled_at",
            ),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
        }),
    )

    def get_fieldsets(self, request, obj=None):
        # Show the spec fieldset matching the order's kind. New orders
        # (obj=None) default to sticker, matching the model default.
        from .models import KIND_CATALOG
        kind = getattr(obj, "kind", None) or "sticker"
        spec_fieldset = (
            self._CATALOG_FIELDSET if kind == KIND_CATALOG else self._STICKER_FIELDSET
        )
        return self._COMMON_FIELDSETS + (spec_fieldset,) + self._TRAILING_FIELDSETS

    @admin.display(description="Item")
    def material_or_product(self, obj):
        if obj.kind == "catalog":
            return obj.product.name if obj.product else "—"
        return obj.material or "—"

    @admin.display(description="Qty")
    def quantity_display(self, obj):
        return obj.product_quantity if obj.kind == "catalog" else obj.quantity
    readonly_fields = (
        "uuid",
        "created_at",
        "updated_at",
        "placed_at",
        "paid_at",
        "shipped_at",
        "delivered_at",
        "cancelled_at",
    )
    inlines = [OrderFileInline]

    @admin.display(description="ID", ordering="uuid")
    def short_pk(self, obj):
        return str(obj.pk)[:8]

    @admin.display(description="Size")
    def size_display(self, obj):
        if not obj.width_mm or not obj.height_mm:
            return "—"
        return f"{obj.width_mm/10:g}×{obj.height_mm/10:g} cm"

    @admin.display(description="Total", ordering="total_amount_cents")
    def total_eur(self, obj):
        if not obj.total_amount_cents:
            return "—"
        return format_html("{:.2f} {}", obj.total_amount_cents / 100, obj.currency)


@admin.register(OrderFile)
class OrderFileAdmin(admin.ModelAdmin):
    list_display = ("short_pk", "order_link", "kind", "mime_type", "size_kb", "created_at")
    list_filter = ("kind",)
    search_fields = ("uuid", "order__uuid")
    ordering = ("-created_at",)
    readonly_fields = ("uuid", "mime_type", "size_bytes", "created_at", "updated_at")

    @admin.display(description="ID")
    def short_pk(self, obj):
        return str(obj.pk)[:8]

    @admin.display(description="Order")
    def order_link(self, obj):
        return str(obj.order_id)[:8]

    @admin.display(description="Size")
    def size_kb(self, obj):
        if not obj.size_bytes:
            return "—"
        return f"{obj.size_bytes / 1024:.1f} KB"
