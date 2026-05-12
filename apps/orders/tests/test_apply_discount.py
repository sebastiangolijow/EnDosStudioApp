"""POST /orders/{uuid}/apply-discount/ — customer applies a promo
code to their draft order, total recomputes with the discount."""
from django.urls import reverse

from apps.discounts.models import Discount
from apps.orders.models import Order
from tests.base import BaseTestCase


def _fill_draft(customer, **overrides):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.orders.models import OrderFile

    defaults = dict(
        created_by=customer,
        status="draft",
        material="vinilo_blanco",
        width_mm=100,
        height_mm=100,
        quantity=100,
        recipient_name="Test",
        street_line_1="C/ Test 1",
        city="Barcelona",
        postal_code="08001",
        country="ES",
        shipping_phone="+34 600 123 456",
    )
    defaults.update(overrides)
    order = Order.objects.create(**defaults)
    OrderFile.objects.create(
        order=order,
        kind="original",
        file=SimpleUploadedFile("t.png", b"\x89PNG fake", content_type="image/png"),
        created_by=customer,
    )
    return order


class ApplyDiscountHappyPathTests(BaseTestCase):
    def test_valid_code_stamps_and_recomputes(self):
        Discount.objects.create(code="WELCOME10", percent_off=10, is_enabled=True)
        customer = self.create_customer()
        order = _fill_draft(customer)
        client = self.authenticate(customer)

        response = client.post(
            reverse("order-apply-discount", kwargs={"pk": order.pk}),
            data={"code": "welcome10"},  # mixed case → normalized to upper
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["discount_code"], "WELCOME10")
        self.assertGreater(response.data["discount_cents"], 0)

        order.refresh_from_db()
        self.assertEqual(order.discount_code, "WELCOME10")
        self.assertGreater(order.discount_cents, 0)
        # Gold-standard order: vinilo_blanco 10×10 q=100 = 5951 cents
        # pre-discount, pre-IVA. 10% off = 595 cents. Post-discount
        # pre-IVA = 5356. ×1.21 = 6480.76 → 6481.
        self.assertEqual(order.discount_cents, 595)
        self.assertEqual(order.total_amount_cents, 6481)


class ApplyDiscountErrorTests(BaseTestCase):
    def test_unknown_code_returns_404(self):
        customer = self.create_customer()
        order = _fill_draft(customer)
        client = self.authenticate(customer)
        response = client.post(
            reverse("order-apply-discount", kwargs={"pk": order.pk}),
            data={"code": "NOPE"},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["detail"], "not_found")
        order.refresh_from_db()
        self.assertEqual(order.discount_cents, 0)

    def test_disabled_code_returns_409(self):
        Discount.objects.create(code="OLDSALE", percent_off=20, is_enabled=False)
        customer = self.create_customer()
        order = _fill_draft(customer)
        client = self.authenticate(customer)
        response = client.post(
            reverse("order-apply-discount", kwargs={"pk": order.pk}),
            data={"code": "OLDSALE"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["detail"], "disabled")

    def test_non_draft_order_rejected(self):
        Discount.objects.create(code="WELCOME10", percent_off=10)
        customer = self.create_customer()
        order = _fill_draft(customer, status="paid")
        client = self.authenticate(customer)
        response = client.post(
            reverse("order-apply-discount", kwargs={"pk": order.pk}),
            data={"code": "WELCOME10"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["detail"], "wrong_status")

    def test_other_customer_cant_apply(self):
        Discount.objects.create(code="WELCOME10", percent_off=10)
        target = self.create_customer()
        order = _fill_draft(target)
        client, _ = self.authenticate_as_customer()  # different user
        response = client.post(
            reverse("order-apply-discount", kwargs={"pk": order.pk}),
            data={"code": "WELCOME10"},
            format="json",
        )
        # Order isn't in the requesting user's queryset → 404
        # (consistent with the rest of the API, no info leak).
        self.assertIn(response.status_code, (403, 404))


class PlaceOrderRevalidatesDiscountTests(BaseTestCase):
    """Re-validation guard: if the admin disables the code between
    apply and place_order, the customer's stored discount silently
    falls back to 0 (the order is placed at full price, no error)."""

    def test_disabled_between_apply_and_place_drops_discount(self):
        d = Discount.objects.create(code="WELCOME10", percent_off=10, is_enabled=True)
        customer = self.create_customer()
        order = _fill_draft(customer)
        client = self.authenticate(customer)

        # Customer applies — discount lands.
        response = client.post(
            reverse("order-apply-discount", kwargs={"pk": order.pk}),
            data={"code": "WELCOME10"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.discount_cents, 595)

        # Admin disables the code.
        d.is_enabled = False
        d.save(update_fields=["is_enabled"])

        # Customer places the order — _recompute_order_total runs
        # again. discount_code is still 'WELCOME10' on the order (the
        # audit trail), but the resolved percent is 0 so the total
        # recovers to the no-discount price.
        place_response = client.post(
            reverse("order-place", kwargs={"pk": order.pk}),
        )
        self.assertEqual(place_response.status_code, 200, place_response.data)
        order.refresh_from_db()
        self.assertEqual(order.discount_code, "WELCOME10")  # audit kept
        self.assertEqual(order.discount_cents, 0)  # but no money off
        # Gold-standard 7201 cents — pre-discount full price restored.
        self.assertEqual(order.total_amount_cents, 7201)


class SerializerExposesDiscountFieldsTests(BaseTestCase):
    def test_order_response_includes_discount_breakdown(self):
        Discount.objects.create(code="WELCOME10", percent_off=10)
        customer = self.create_customer()
        order = _fill_draft(customer)
        client = self.authenticate(customer)

        client.post(
            reverse("order-apply-discount", kwargs={"pk": order.pk}),
            data={"code": "WELCOME10"},
            format="json",
        )
        response = client.get(reverse("order-detail", kwargs={"pk": order.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["discount_code"], "WELCOME10")
        self.assertEqual(response.data["discount_cents"], 595)
        self.assertEqual(response.data["discount_eur"], "5.95")
        # Subtotal should be the PRE-discount work amount so the
        # summary card reads: subtotal − discount + IVA = total.
        # Gold standard: 5951 pre-discount; 5951 - 595 = 5356; × 1.21
        # = 6481 total. subtotal_cents derived from total / 1.21 +
        # discount_cents = 5356 + 595 = 5951.
        self.assertEqual(response.data["subtotal_cents"], 5951)
        self.assertEqual(response.data["total_amount_cents"], 6481)
