from django.contrib import admin
from django.utils.html import format_html

from .models import PaymentIntent


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    """Read-only mirror of Stripe state. Don't edit here — Stripe is the source of truth."""

    list_display = (
        "stripe_payment_intent_id",
        "status",
        "order_link",
        "amount_eur",
        "currency",
        "created_at",
    )
    list_filter = ("status", "currency")
    search_fields = ("stripe_payment_intent_id", "order__uuid")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"

    fieldsets = (
        ("Stripe", {
            "fields": ("stripe_payment_intent_id", "status", "amount_cents", "currency"),
        }),
        ("Order", {
            "fields": ("order",),
        }),
        ("Raw event", {
            "fields": ("raw_event",),
            "classes": ("collapse",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        # Everything is read-only — Stripe owns the truth.
        return [f.name for f in self.model._meta.fields] + ["raw_event"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Order")
    def order_link(self, obj):
        return str(obj.order_id)[:8]

    @admin.display(description="Amount", ordering="amount_cents")
    def amount_eur(self, obj):
        return format_html("{:.2f}", obj.amount_cents / 100)
