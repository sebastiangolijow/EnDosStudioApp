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
# Gold-standard: vinilo_blanco 10×10 cm × q=100 → pre-IVA 5951 cents,
# all-in 5951 × 1.21 = 7200.71 → 7201 cents.
GOLD_BASELINE_CENTS = 7201


class ShippingMultiplierTests(BaseTestCase):
    """Pure pricing function — no DB."""

    def test_normal_shipping_is_baseline(self):
        cents = compute_total_cents(**GOLD_KWARGS, shipping_method="normal")
        self.assertEqual(cents, GOLD_BASELINE_CENTS)

    def test_express_adds_20_percent(self):
        # pre-IVA: 5951.25 × 1.20 = 7141.5 → 7142
        # all-in:  7142 × 1.21 = 8641.82 → 8642
        cents = compute_total_cents(**GOLD_KWARGS, shipping_method="express")
        self.assertEqual(cents, 8642)

    def test_flash_adds_60_percent(self):
        # pre-IVA: 5951.25 × 1.60 = 9522
        # all-in:  9522 × 1.21 = 11521.62 → 11522
        cents = compute_total_cents(**GOLD_KWARGS, shipping_method="flash")
        self.assertEqual(cents, 11522)

    def test_shipping_stacks_with_existing_addons(self):
        # All toggles on + flash: 1 + 0.35 + 0.35 + 0.20 + 0.20 + 0.60 = 2.70x
        # pre-IVA: 5951.25 × 2.70 = 16068.375 → 16068
        # all-in:  16068 × 1.21 = 19442.28 → 19442
        cents = compute_total_cents(
            **GOLD_KWARGS,
            with_relief=True,
            with_tinta_blanca=True,
            with_barniz_brillo=True,
            with_barniz_opaco=True,
            shipping_method="flash",
        )
        self.assertEqual(cents, 19442)

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
        # express + IVA: 7142 × 1.21 = 8641.82 → 8642 cents (86.42 €).
        self.assertEqual(res.data["total_amount_cents"], 8642)
        # IVA breakdown returned alongside total. Subtotal = total / 1.21.
        # 8642 / 1.21 = 7142.149… → 7142; IVA = 8642 − 7142 = 1500.
        self.assertEqual(res.data["subtotal_cents"], 7142)
        self.assertEqual(res.data["iva_cents"], 1500)


class ShippingContactTests(BaseTestCase):
    """shipping_phone + shipping_email on Order."""

    def _make_draft_with_address(self, customer):
        """Fully-populated draft EXCEPT shipping_phone — so place_order
        can be tested against the new required-field rule."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        from apps.orders.models import OrderFile

        order = Order.objects.create(
            created_by=customer,
            status="draft",
            material="vinilo_blanco",
            width_mm=100,
            height_mm=100,
            quantity=100,
            recipient_name="Test Recipient",
            street_line_1="Carrer 1",
            city="Barcelona",
            postal_code="08001",
            country="ES",
        )
        OrderFile.objects.create(
            order=order,
            kind="original",
            file=SimpleUploadedFile("test.png", b"\x89PNG fake", content_type="image/png"),
            created_by=customer,
        )
        return order

    def test_shipping_phone_email_round_trip_via_patch(self):
        client, customer = self.authenticate_as_customer()
        order = self._make_draft_with_address(customer)
        res = client.patch(
            reverse("order-detail", kwargs={"pk": order.pk}),
            data={
                "shipping_phone": "+34 611 222 333",
                "shipping_email": "alt@example.com",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["shipping_phone"], "+34 611 222 333")
        self.assertEqual(res.data["shipping_email"], "alt@example.com")
        order.refresh_from_db()
        self.assertEqual(order.shipping_phone, "+34 611 222 333")
        self.assertEqual(order.shipping_email, "alt@example.com")

    def test_place_order_requires_shipping_phone(self):
        """place_order should 409 when shipping_phone is missing — same
        guard as recipient_name/street/city/postal_code/country."""
        client, customer = self.authenticate_as_customer()
        order = self._make_draft_with_address(customer)
        # No shipping_phone set.
        res = client.post(reverse("order-place", kwargs={"pk": order.pk}))
        self.assertEqual(res.status_code, 409)
        self.assertIn("shipping_phone", res.data.get("detail", ""))

    def test_place_order_succeeds_with_shipping_phone(self):
        client, customer = self.authenticate_as_customer()
        order = self._make_draft_with_address(customer)
        order.shipping_phone = "+34 600 999 888"
        order.save(update_fields=["shipping_phone"])
        res = client.post(reverse("order-place", kwargs={"pk": order.pk}))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], "placed")
