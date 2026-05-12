"""Promo / discount codes the shop owner can issue to customers.

The lifecycle is intentionally simple:

  - Owner creates a Discount with a code + percent_off in the admin UI.
  - Customer types the code in the checkout form; the apply-discount
    endpoint stamps order.discount_code + order.discount_cents on the
    draft and recomputes the total.
  - Codes can be enabled / disabled. Disabled codes are still kept (so
    past orders that used them keep their discount_code audit trail).

What we DON'T model today (out of scope, easy to add later if needed):
  - Per-customer redemption limits (whitelist / single-use). Today any
    customer can use any enabled code any number of times.
  - Expiration dates. Use is_enabled to retire codes for now.
  - Min-order constraints / category restrictions.

Pricing math lives in apps.orders.services.compute_total_cents — the
discount is applied AFTER the €20 work floor and BEFORE the 21% IVA.
"""
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import BaseModel


class Discount(BaseModel):
    """A promo code the shop owner has issued.

    Codes are stored UPPERCASE; the model normalizes on save so the
    admin can type 'summer2026' and the customer can submit 'Summer2026'
    and both resolve to 'SUMMER2026'. Uniqueness is enforced at the
    DB level on the normalized form.
    """

    code = models.CharField(
        _("code"),
        max_length=40,
        unique=True,
        help_text=_("Customer-facing code. Normalized to uppercase."),
    )
    percent_off = models.PositiveSmallIntegerField(
        _("percent off"),
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text=_("1 to 100. Applied to the pre-IVA work subtotal."),
    )
    is_enabled = models.BooleanField(
        _("is enabled"),
        default=True,
        db_index=True,
        help_text=_("Disabled codes still exist for the order audit trail."),
    )

    history = HistoricalRecords(
        history_user_id_field=models.UUIDField(null=True, blank=True),
    )

    class Meta:
        db_table = "discounts_discount"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_enabled", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.code} (-{self.percent_off}%)"

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip().upper()
        super().save(*args, **kwargs)
