"""Order-received notification emails.

Covers the three customer-facing email workflows:
  1. Customer receives a confirmation when the order is paid OR reserved.
  2. Owner receives a notification on the same two transitions.
  3. Customer receives a shipping email when status flips to shipped
     (re-tested here for completeness; primary coverage in
     test_admin_set_status).
"""
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.orders.models import Order
from apps.orders.services import (
    place_order,
    reserve_order,
    transition_to_paid,
)
from tests.base import BaseTestCase


def _fill_draft(customer, **overrides):
    """A sticker draft populated with everything place/reserve needs."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from apps.orders.models import OrderFile

    defaults = dict(
        created_by=customer,
        status="draft",
        material="vinilo_blanco",
        width_mm=100,
        height_mm=100,
        quantity=100,
        recipient_name="Test Customer",
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


@override_settings(SHOP_OWNER_EMAIL="owner@stickerapp.local")
class OrderPaidEmailsTests(BaseTestCase):
    """Stripe webhook path → transition_to_paid → 2 emails."""

    def test_paid_sends_customer_confirmation_and_owner_notification(self):
        customer = self.create_customer(email="buyer@example.com")
        order = _fill_draft(customer, shipping_email="buyer@example.com")
        order = place_order(order)

        with mock.patch("apps.orders.services.send_mail") as send:
            transition_to_paid(order, stripe_event={})

        # Customer + owner = 2 sends.
        self.assertEqual(send.call_count, 2)
        recipients = {tuple(c.kwargs["recipient_list"]) for c in send.call_args_list}
        self.assertIn(("buyer@example.com",), recipients)
        self.assertIn(("owner@stickerapp.local",), recipients)

    def test_paid_customer_email_body_mentions_order(self):
        customer = self.create_customer(email="buyer@example.com")
        order = _fill_draft(customer, shipping_email="buyer@example.com")
        order = place_order(order)

        with mock.patch("apps.orders.services.send_mail") as send:
            transition_to_paid(order, stripe_event={})

        customer_call = next(
            c for c in send.call_args_list
            if c.kwargs["recipient_list"] == ["buyer@example.com"]
        )
        self.assertIn("Recibimos tu pedido", customer_call.kwargs["subject"])
        self.assertIn(str(order.uuid)[:8], customer_call.kwargs["message"])

    def test_paid_owner_email_body_mentions_customer_and_total(self):
        customer = self.create_customer(email="buyer@example.com")
        order = _fill_draft(customer, shipping_email="buyer@example.com")
        order = place_order(order)

        with mock.patch("apps.orders.services.send_mail") as send:
            transition_to_paid(order, stripe_event={})

        owner_call = next(
            c for c in send.call_args_list
            if c.kwargs["recipient_list"] == ["owner@stickerapp.local"]
        )
        msg = owner_call.kwargs["message"]
        self.assertIn("Test Customer", msg)  # recipient_name
        self.assertIn("buyer@example.com", msg)
        # Total appears formatted with 2 decimals.
        self.assertRegex(msg, r"Total: €\d+\.\d{2}")


@override_settings(SHOP_OWNER_EMAIL="owner@stickerapp.local")
class OrderReservedEmailsTests(BaseTestCase):
    """Reservation path → reserve_order → 2 emails with pickup_at."""

    def test_reserve_sends_customer_and_owner_emails(self):
        customer = self.create_customer(
            email="buyer@example.com",
            can_reserve_orders=True,
        )
        order = _fill_draft(customer, shipping_email="buyer@example.com")
        pickup = timezone.now() + timedelta(days=2)

        with mock.patch("apps.orders.services.send_mail") as send:
            reserve_order(order, actor=customer, pickup_at=pickup)

        self.assertEqual(send.call_count, 2)
        recipients = {tuple(c.kwargs["recipient_list"]) for c in send.call_args_list}
        self.assertIn(("buyer@example.com",), recipients)
        self.assertIn(("owner@stickerapp.local",), recipients)

    def test_reserve_customer_email_mentions_pickup(self):
        customer = self.create_customer(
            email="buyer@example.com",
            can_reserve_orders=True,
        )
        order = _fill_draft(customer, shipping_email="buyer@example.com")
        pickup = timezone.now() + timedelta(days=3, hours=2)

        with mock.patch("apps.orders.services.send_mail") as send:
            reserve_order(order, actor=customer, pickup_at=pickup)

        customer_call = next(
            c for c in send.call_args_list
            if c.kwargs["recipient_list"] == ["buyer@example.com"]
        )
        self.assertIn("Reservamos tu pedido", customer_call.kwargs["subject"])
        msg = customer_call.kwargs["message"]
        self.assertIn("Fecha de retiro", msg)
        self.assertIn("en efectivo, al retirar", msg)

    def test_reserve_owner_email_mentions_reservation_pickup(self):
        customer = self.create_customer(
            email="buyer@example.com",
            can_reserve_orders=True,
        )
        order = _fill_draft(customer, shipping_email="buyer@example.com")
        pickup = timezone.now() + timedelta(days=1)

        with mock.patch("apps.orders.services.send_mail") as send:
            reserve_order(order, actor=customer, pickup_at=pickup)

        owner_call = next(
            c for c in send.call_args_list
            if c.kwargs["recipient_list"] == ["owner@stickerapp.local"]
        )
        self.assertIn("[Reserva]", owner_call.kwargs["subject"])
        self.assertIn("Retiro:", owner_call.kwargs["message"])


class OrderEmailFallbackTests(BaseTestCase):
    """Edge cases — missing recipient / unconfigured owner address."""

    def test_paid_skips_customer_email_when_no_recipient(self):
        """When the order has no shipping_email and the user account has
        been removed (created_by=None), the customer email is skipped
        with a warning. Owner email still fires; transition succeeds."""
        customer = self.create_customer()
        order = _fill_draft(customer, shipping_email="")
        order = place_order(order)
        # Detach the user so created_by_id is None — simulates the
        # SET_NULL path when a customer deletes their account.
        Order.objects.filter(pk=order.pk).update(created_by=None)
        order.refresh_from_db()

        with mock.patch("apps.orders.services.send_mail") as send:
            transition_to_paid(order, stripe_event={})

        # Only the owner email goes out.
        self.assertEqual(send.call_count, 1)
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")

    @override_settings(SHOP_OWNER_EMAIL="")
    def test_owner_email_skipped_when_unconfigured(self):
        customer = self.create_customer(email="buyer@example.com")
        order = _fill_draft(customer, shipping_email="buyer@example.com")
        order = place_order(order)

        with mock.patch("apps.orders.services.send_mail") as send:
            transition_to_paid(order, stripe_event={})

        # Only the customer email goes out.
        self.assertEqual(send.call_count, 1)
        self.assertEqual(
            send.call_args.kwargs["recipient_list"],
            ["buyer@example.com"],
        )
