"""
Payment records.

PaymentIntent is a local mirror of a Stripe PaymentIntent. Stripe is the
source of truth; we keep this table for reporting + debugging webhook
flows. Don't store card data here ever.

PROTECT on the order FK because deleting an order with payment history
should be a deliberate operation, not a cascade. Cancellation is a
status on Order, not a row delete.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import BaseModel


class PaymentIntent(BaseModel):
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.PROTECT,
        related_name="payment_intents",
    )
    stripe_payment_intent_id = models.CharField(
        _("stripe payment intent id"),
        max_length=255,
        unique=True,
        db_index=True,
    )
    # Free-form mirror of Stripe's status set (requires_payment_method,
    # processing, succeeded, canceled, ...). Don't enum it; Stripe adds states.
    status = models.CharField(_("status"), max_length=40)
    amount_cents = models.PositiveIntegerField(_("amount (cents)"))
    currency = models.CharField(_("currency"), max_length=3, default="EUR")
    # Most recent webhook payload for this PI. Overwritten on each event.
    raw_event = models.JSONField(_("raw event"), default=dict, blank=True)

    class Meta:
        db_table = "payments_paymentintent"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["order", "-created_at"]),
        ]

    def __str__(self):
        return f"PaymentIntent {self.stripe_payment_intent_id} ({self.status})"
