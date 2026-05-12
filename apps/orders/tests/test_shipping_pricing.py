"""Tests for the shipping_method pricing multiplier.

Adds three additive surcharges (0% / 20% / 60%) on top of the existing
add-on stacking. Covers:
  - compute_total_cents shipping branches (normal/express/flash + unknown)
  - PATCH accepts shipping_method
  - GET /quote/ accepts shipping_method query param
  - shipping_method round-trips through OrderSerializer
"""
from django.urls import reverse

from apps.orders.models import Order
from apps.orders.services import (
    InvalidPricingInput,
    compute_total_cents,
)
from tests.base import BaseTestCase


# Gold-standard scenario from CLAUDE.md so the math is easy to verify by
# hand: vinilo_blanco 10×10 cm × q=100 → 5951.25 cents (≈ 59.51 €).
GOLD_KWARGS = dict(
    material="vinilo_blanco",
    width_mm=100,
    height_mm=100,
    quantity=100,
)
GOLD_BASELINE_CENTS = 5951


class ShippingMultiplierTests(BaseTestCase):
    """Pure pricing function — no DB."""

    def test_normal_shipping_is_baseline(self):
        cents = compute_total_cents(**GOLD_KWARGS, shipping_method="normal")
        self.assertEqual(cents, GOLD_BASELINE_CENTS)

    def test_express_adds_20_percent(self):
        # 5951.25 × 1.20 = 7141.5 → 7142 cents
        cents = compute_total_cents(**GOLD_KWARGS, shipping_method="express")
        self.assertEqual(cents, 7142)

    def test_flash_adds_60_percent(self):
        # 5951.25 × 1.60 = 9522 cents
        cents = compute_total_cents(**GOLD_KWARGS, shipping_method="flash")
        self.assertEqual(cents, 9522)

    def test_shipping_stacks_with_existing_addons(self):
        # All toggles on + flash: 1 + 0.35 + 0.35 + 0.20 + 0.20 + 0.60 = 2.70x
        # 5951.25 × 2.70 = 16068.375 → 16068 cents
        cents = compute_total_cents(
            **GOLD_KWARGS,
            with_relief=True,
            with_tinta_blanca=True,
            with_barniz_brillo=True,
            with_barniz_opaco=True,
            shipping_method="flash",
        )
        self.assertEqual(cents, 16068)

    def test_unknown_shipping_method_raises(self):
        with self.assertRaises(InvalidPricingInput):
            compute_total_cents(**GOLD_KWARGS, shipping_method="overnight_yacht")

    def test_default_shipping_method_is_normal(self):
        # Callers that don't pass shipping_method still get the baseline.
        cents = compute_total_cents(**GOLD_KWARGS)
        self.assertEqual(cents, GOLD_BASELINE_CENTS)


class ShippingMethodAPITests(BaseTestCase):
    """shipping_method round-trips through the order API surface."""

    def _make_draft(self, customer):
        return Order.objects.create(
            created_by=customer,
            status="draft",
            material="vinilo_blanco",
            width_mm=100,
            height_mm=100,
            quantity=100,
        )

    def test_default_shipping_method_in_response_is_normal(self):
        client, customer = self.authenticate_as_customer()
        order = self._make_draft(customer)
        res = client.get(reverse("order-detail", kwargs={"pk": order.pk}))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["shipping_method"], "normal")

    def test_patch_updates_shipping_method(self):
        client, customer = self.authenticate_as_customer()
        order = self._make_draft(customer)
        res = client.patch(
            reverse("order-detail", kwargs={"pk": order.pk}),
            data={"shipping_method": "express"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["shipping_method"], "express")
        order.refresh_from_db()
        self.assertEqual(order.shipping_method, "express")

    def test_patch_rejects_unknown_shipping_method(self):
        client, customer = self.authenticate_as_customer()
        order = self._make_draft(customer)
        res = client.patch(
            reverse("order-detail", kwargs={"pk": order.pk}),
            data={"shipping_method": "overnight_yacht"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_quote_endpoint_accepts_shipping_method(self):
        client, _ = self.authenticate_as_customer()
        res = client.get(
            reverse("order-quote"),
            data={
                "material": "vinilo_blanco",
                "width_mm": 100,
                "height_mm": 100,
                "quantity": 100,
                "shipping_method": "express",
            },
        )
        self.assertEqual(res.status_code, 200)
        # 7142 cents == 71.42 €
        self.assertEqual(res.data["total_amount_cents"], 7142)
