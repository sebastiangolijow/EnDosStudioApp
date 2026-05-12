"""Reservation flow — in-store pickup for whitelisted customers.

Covers the customer-only POST /orders/{uuid}/reserve/ endpoint that
transitions draft|placed → reserved. Owner accepts cash at pickup and
flips to 'paid' via admin-set-status (tested in test_admin_set_status).
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from apps.orders.models import Order
from tests.base import BaseTestCase


def _fill_draft(customer, **overrides):
    """A sticker draft populated with everything `reserve` requires."""
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
        file=SimpleUploadedFile("test.png", b"\x89PNG fake", content_type="image/png"),
        created_by=customer,
    )
    return order


class ReserveWhitelistGateTests(BaseTestCase):
    def test_non_whitelisted_customer_gets_403(self):
        customer = self.create_customer(can_reserve_orders=False)
        order = _fill_draft(customer)
        client = self.authenticate(customer)

        response = client.post(
            reverse("order-reserve", kwargs={"pk": order.pk}),
            data={"pickup_at": (timezone.now() + timedelta(days=2)).isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, "draft")

    def test_anon_blocked(self):
        customer = self.create_customer(can_reserve_orders=True)
        order = _fill_draft(customer)
        response = self.client.post(
            reverse("order-reserve", kwargs={"pk": order.pk}),
            data={"pickup_at": (timezone.now() + timedelta(days=2)).isoformat()},
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))


class ReserveSuccessTests(BaseTestCase):
    def test_whitelisted_customer_reserves_draft(self):
        customer = self.create_customer(can_reserve_orders=True)
        order = _fill_draft(customer)
        client = self.authenticate(customer)
        pickup = timezone.now() + timedelta(days=2, hours=3)

        response = client.post(
            reverse("order-reserve", kwargs={"pk": order.pk}),
            data={"pickup_at": pickup.isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, "reserved")
        self.assertIsNotNone(order.pickup_at)
        self.assertIsNotNone(order.reserved_at)
        # place_order's total computation also ran — the owner needs an
        # amount to take cash for.
        self.assertGreater(order.total_amount_cents, 0)

    def test_reserved_response_includes_pickup_at(self):
        customer = self.create_customer(can_reserve_orders=True)
        order = _fill_draft(customer)
        client = self.authenticate(customer)
        pickup = timezone.now() + timedelta(days=1)

        response = client.post(
            reverse("order-reserve", kwargs={"pk": order.pk}),
            data={"pickup_at": pickup.isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNotNone(response.data["pickup_at"])
        self.assertEqual(response.data["status"], "reserved")


class ReserveValidationTests(BaseTestCase):
    def test_past_pickup_rejected(self):
        customer = self.create_customer(can_reserve_orders=True)
        order = _fill_draft(customer)
        client = self.authenticate(customer)

        response = client.post(
            reverse("order-reserve", kwargs={"pk": order.pk}),
            data={"pickup_at": (timezone.now() - timedelta(hours=1)).isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.status, "draft")

    def test_missing_pickup_at_rejected(self):
        customer = self.create_customer(can_reserve_orders=True)
        order = _fill_draft(customer)
        client = self.authenticate(customer)

        response = client.post(
            reverse("order-reserve", kwargs={"pk": order.pk}),
            data={},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_already_paid_order_rejected(self):
        customer = self.create_customer(can_reserve_orders=True)
        order = _fill_draft(customer, status="paid")
        client = self.authenticate(customer)

        response = client.post(
            reverse("order-reserve", kwargs={"pk": order.pk}),
            data={"pickup_at": (timezone.now() + timedelta(days=1)).isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, 409)


class MeEndpointReturnsCanReserveTests(BaseTestCase):
    def test_me_includes_can_reserve_orders(self):
        customer = self.create_customer(can_reserve_orders=True)
        client = self.authenticate(customer)
        response = client.get(reverse("current-user"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["can_reserve_orders"])

    def test_me_default_can_reserve_is_false(self):
        customer = self.create_customer()
        client = self.authenticate(customer)
        response = client.get(reverse("current-user"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["can_reserve_orders"])
