"""Stripe webhook integration tests.

Cover the contract the StripeWebhookView promises:
  - Valid succeeded event → Order moves to "paid" + PaymentIntent recorded.
  - Replay of the same succeeded event → idempotent (no double-paid, no error).
  - Unknown event type → 200 with no side effects.
  - Invalid signature → 400.
  - Succeeded event for an order we can't find → 200 (don't 5xx and trigger
    Stripe retry storms; log loudly instead).

We mock StripeService.construct_webhook_event so tests don't need a real
Stripe webhook secret. The view's behaviour after that point is what we
care about — the SDK call itself is the SDK's problem.
"""
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.orders.models import Order, OrderFile
from apps.orders.services import place_order
from apps.payments.models import PaymentIntent
from tests.base import BaseTestCase


WEBHOOK_URL = "/api/v1/payments/webhooks/stripe/"


def _make_pi_event(*, event_type, pi_id, order_uuid=None, amount=11000, status="succeeded"):
    """Build a minimal Stripe event dict shaped like the SDK's parsed output."""
    metadata = {"order_uuid": str(order_uuid)} if order_uuid else {}
    return {
        "id": f"evt_{pi_id}",
        "type": event_type,
        "data": {
            "object": {
                "id": pi_id,
                "status": status,
                "amount": amount,
                "currency": "eur",
                "metadata": metadata,
            }
        },
    }


class StripeWebhookTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.customer = self.create_customer()
        # Build a minimum viable placed order so transition_to_paid will fire.
        self.order = Order.objects.create(created_by=self.customer)
        self.order.material = "holografico"
        self.order.width_mm = 50
        self.order.height_mm = 50
        self.order.quantity = 50
        self.order.recipient_name = "Test Recipient"
        self.order.street_line_1 = "Carrer Test 1"
        self.order.city = "Barcelona"
        self.order.postal_code = "08001"
        self.order.country = "ES"
        self.order.save()
        OrderFile.objects.create(
            order=self.order,
            kind="original",
            file=SimpleUploadedFile("test.png", b"\x89PNG fake", content_type="image/png"),
            created_by=self.customer,
        )
        self.order = place_order(self.order)
        self.assertEqual(self.order.status, "placed")

    def _post_event(self, event):
        """POST a (mocked) webhook event and return the response."""
        with mock.patch(
            "apps.payments.views.StripeService.construct_webhook_event",
            return_value=event,
        ):
            return self.client.post(
                WEBHOOK_URL,
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="t=1,v1=fake",
            )

    def test_succeeded_event_transitions_order_and_records_payment_intent(self):
        event = _make_pi_event(
            event_type="payment_intent.succeeded",
            pi_id="pi_test_succeed_1",
            order_uuid=self.order.pk,
            amount=11000,
        )
        response = self._post_event(event)

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")
        self.assertIsNotNone(self.order.paid_at)

        pi = PaymentIntent.objects.get(stripe_payment_intent_id="pi_test_succeed_1")
        self.assertEqual(pi.order, self.order)
        self.assertEqual(pi.status, "succeeded")
        self.assertEqual(pi.amount_cents, 11000)
        self.assertEqual(pi.currency, "EUR")

    def test_succeeded_event_replay_is_idempotent(self):
        event = _make_pi_event(
            event_type="payment_intent.succeeded",
            pi_id="pi_test_replay",
            order_uuid=self.order.pk,
        )
        # First delivery
        first = self._post_event(event)
        self.assertEqual(first.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")
        first_paid_at = self.order.paid_at

        # Replay — Stripe retried for whatever reason
        second = self._post_event(event)
        self.assertEqual(second.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")
        # paid_at MUST NOT move — that's the idempotency guarantee
        self.assertEqual(self.order.paid_at, first_paid_at)

        # Still exactly one PaymentIntent row for this PI id
        self.assertEqual(
            PaymentIntent.objects.filter(stripe_payment_intent_id="pi_test_replay").count(),
            1,
        )

    def test_unknown_event_type_is_acknowledged_with_no_side_effects(self):
        event = {
            "id": "evt_unknown",
            "type": "customer.subscription.created",  # not in HANDLED_EVENT_TYPES
            "data": {"object": {"id": "sub_xxx"}},
        }
        response = self._post_event(event)

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "placed")  # untouched
        self.assertFalse(PaymentIntent.objects.exists())

    def test_invalid_signature_returns_400(self):
        with mock.patch(
            "apps.payments.views.StripeService.construct_webhook_event",
            side_effect=ValueError("bad signature"),
        ):
            response = self.client.post(
                WEBHOOK_URL,
                data=b"{}",
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="bad",
            )
        self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "placed")

    def test_succeeded_event_for_unknown_order_returns_200(self):
        # Stripe sent us a real event but with no order_uuid metadata and a
        # PI id that doesn't match any local order. Shouldn't 5xx.
        event = _make_pi_event(
            event_type="payment_intent.succeeded",
            pi_id="pi_orphan",
            order_uuid=None,  # no metadata
        )
        response = self._post_event(event)

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "placed")
        self.assertFalse(PaymentIntent.objects.exists())

    def test_payment_failed_event_records_intent_but_does_not_transition(self):
        event = _make_pi_event(
            event_type="payment_intent.payment_failed",
            pi_id="pi_test_failed",
            order_uuid=self.order.pk,
            status="requires_payment_method",
        )
        response = self._post_event(event)

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "placed")  # NOT paid

        pi = PaymentIntent.objects.get(stripe_payment_intent_id="pi_test_failed")
        self.assertEqual(pi.status, "requires_payment_method")

    def test_succeeded_event_finds_order_via_stripe_payment_intent_id_fallback(self):
        # Drop metadata; instead, denormalize the PI id onto the Order
        # (this is what the future checkout endpoint will do).
        pi_id = "pi_via_fallback"
        self.order.stripe_payment_intent_id = pi_id
        self.order.save(update_fields=["stripe_payment_intent_id"])

        event = _make_pi_event(
            event_type="payment_intent.succeeded",
            pi_id=pi_id,
            order_uuid=None,  # no metadata
        )
        response = self._post_event(event)

        self.assertEqual(response.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")
