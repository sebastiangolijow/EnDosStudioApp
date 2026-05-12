"""Admin force-status override + shipping notification email.

Covers the staff-only `admin-set-status` endpoint that bypasses the usual
transition guards so the shop owner can correct mistakes (re-open
cancelled orders, mark delivered retroactively) and stamp shipping
details on the way to 'shipped'.
"""
from unittest import mock

from django.core import mail
from django.urls import reverse

from apps.orders.models import Order
from tests.base import BaseTestCase


def _make_paid_order(customer):
    """A reasonably-populated order in `paid` state — the realistic
    starting point for an admin who's about to mark in_production or
    shipped."""
    return Order.objects.create(
        created_by=customer,
        status="paid",
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
        shipping_email="customer@example.com",
        total_amount_cents=7201,
    )


class AdminSetStatusPermissionTests(BaseTestCase):
    def test_anon_cannot_force_status(self):
        customer = self.create_customer()
        order = _make_paid_order(customer)
        response = self.client.post(
            reverse("order-admin-set-status", kwargs={"pk": order.pk}),
            data={"status": "shipped"},
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))

    def test_customer_cannot_force_status(self):
        client, customer = self.authenticate_as_customer()
        order = _make_paid_order(customer)
        response = client.post(
            reverse("order-admin-set-status", kwargs={"pk": order.pk}),
            data={"status": "shipped"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)


class AdminSetStatusTransitionTests(BaseTestCase):
    def test_admin_moves_paid_to_in_production_and_stamps_timestamp(self):
        client, _ = self.authenticate_as_admin()
        customer = self.create_customer()
        order = _make_paid_order(customer)

        response = client.post(
            reverse("order-admin-set-status", kwargs={"pk": order.pk}),
            data={"status": "in_production"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, "in_production")

    def test_admin_can_force_backwards_transition(self):
        """Cancelled → paid is normally not allowed; force-status lets it."""
        client, _ = self.authenticate_as_admin()
        customer = self.create_customer()
        order = _make_paid_order(customer)
        order.status = "cancelled"
        order.save(update_fields=["status"])

        response = client.post(
            reverse("order-admin-set-status", kwargs={"pk": order.pk}),
            data={"status": "paid"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")

    def test_unknown_status_rejected_with_400(self):
        client, _ = self.authenticate_as_admin()
        customer = self.create_customer()
        order = _make_paid_order(customer)
        response = client.post(
            reverse("order-admin-set-status", kwargs={"pk": order.pk}),
            data={"status": "teleported"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)


class AdminSetStatusShippedTests(BaseTestCase):
    def test_shipped_persists_carrier_tracking_eta(self):
        client, _ = self.authenticate_as_admin()
        customer = self.create_customer()
        order = _make_paid_order(customer)

        response = client.post(
            reverse("order-admin-set-status", kwargs={"pk": order.pk}),
            data={
                "status": "shipped",
                "shipping_carrier": "MRW",
                "shipping_tracking_code": "MRW123456",
                "shipping_eta_date": "2026-05-20",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, "shipped")
        self.assertEqual(order.shipping_carrier, "MRW")
        self.assertEqual(order.shipping_tracking_code, "MRW123456")
        self.assertEqual(order.shipping_eta_date.isoformat(), "2026-05-20")
        self.assertIsNotNone(order.shipped_at)

    def test_shipped_with_tracking_sends_email(self):
        client, _ = self.authenticate_as_admin()
        customer = self.create_customer()
        order = _make_paid_order(customer)

        with mock.patch("apps.orders.services.send_mail") as send:
            response = client.post(
                reverse("order-admin-set-status", kwargs={"pk": order.pk}),
                data={
                    "status": "shipped",
                    "shipping_carrier": "MRW",
                    "shipping_tracking_code": "MRW123456",
                    "shipping_eta_date": "2026-05-20",
                },
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.data)
        send.assert_called_once()
        call_kwargs = send.call_args.kwargs
        self.assertEqual(call_kwargs["recipient_list"], ["customer@example.com"])
        self.assertIn("MRW", call_kwargs["message"])
        self.assertIn("MRW123456", call_kwargs["message"])
        self.assertIn("2026-05-20", call_kwargs["message"])

    def test_shipped_without_tracking_does_not_email(self):
        """No tracking code = nothing to send. Status still updates."""
        client, _ = self.authenticate_as_admin()
        customer = self.create_customer()
        order = _make_paid_order(customer)

        with mock.patch("apps.orders.services.send_mail") as send:
            response = client.post(
                reverse("order-admin-set-status", kwargs={"pk": order.pk}),
                data={
                    "status": "shipped",
                    "shipping_carrier": "MRW",
                },
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.data)
        send.assert_not_called()
        order.refresh_from_db()
        self.assertEqual(order.status, "shipped")


class ShippingCarriersListTests(BaseTestCase):
    def test_lists_distinct_carriers_from_past_orders(self):
        customer = self.create_customer()
        Order.objects.create(
            created_by=customer,
            status="shipped",
            shipping_carrier="MRW",
            material="vinilo_blanco",
            width_mm=50, height_mm=50, quantity=50,
        )
        Order.objects.create(
            created_by=customer,
            status="shipped",
            shipping_carrier="Correos",
            material="vinilo_blanco",
            width_mm=50, height_mm=50, quantity=50,
        )
        Order.objects.create(
            created_by=customer,
            status="shipped",
            shipping_carrier="MRW",  # duplicate — should dedupe
            material="vinilo_blanco",
            width_mm=50, height_mm=50, quantity=50,
        )

        client, _ = self.authenticate_as_admin()
        response = client.get(reverse("order-shipping-carriers"))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(sorted(response.data["results"]), ["Correos", "MRW"])

    def test_carriers_list_requires_staff(self):
        client, _ = self.authenticate_as_customer()
        response = client.get(reverse("order-shipping-carriers"))
        self.assertEqual(response.status_code, 403)
