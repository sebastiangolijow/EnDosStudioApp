"""
Stripe webhook endpoint.

Stripe POSTs to /api/v1/payments/webhooks/stripe/. We:

  1. Verify the signature against STRIPE_WEBHOOK_SECRET. Bad sig → 400.
  2. Look up the local Order via metadata.order_uuid (preferred) or
     the denormalized stripe_payment_intent_id field on Order.
  3. Record/upsert a local PaymentIntent row (so the shop has a
     paper trail independent of the Stripe dashboard).
  4. For payment_intent.succeeded, transition the order to "paid".
     Idempotent: replays of the same event don't double-transition,
     and a webhook for an already-paid order is a no-op success.
  5. For unknown event types, log + 200 so Stripe stops retrying.

ALWAYS return 200 once we've successfully *received* the event, even
if local lookup/processing fails for a reason that's not Stripe's
problem (e.g. order missing, race). 5xx tells Stripe to retry, which
amplifies bad state. We log loudly instead.
"""
import logging

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.orders.models import Order
from apps.orders.services import InvalidTransition, transition_to_paid

from .services import StripeService, record_payment_intent_event

logger = logging.getLogger(__name__)


# Stripe events we actually act on. Anything else is logged and ack'd.
HANDLED_EVENT_TYPES = {
    "payment_intent.succeeded",
    "payment_intent.payment_failed",
    "payment_intent.canceled",
}


@method_decorator(csrf_exempt, name="dispatch")
class StripeWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        signature = request.headers.get("Stripe-Signature", "")
        payload = request.body

        try:
            event = StripeService().construct_webhook_event(payload, signature)
        except Exception as e:
            logger.warning("Stripe webhook signature failed: %s", e)
            return Response({"detail": "invalid signature"}, status=status.HTTP_400_BAD_REQUEST)

        event_type = event.get("type", "")
        event_id = event.get("id", "")
        logger.info("Stripe webhook received: type=%s id=%s", event_type, event_id)

        if event_type not in HANDLED_EVENT_TYPES:
            # Unhandled event types are still ack'd so Stripe doesn't retry.
            return Response({"detail": "ignored"}, status=status.HTTP_200_OK)

        order = _find_order_for_event(event)
        if order is None:
            # Stripe sent us a real event but we can't tie it to a local order.
            # Possible causes: out-of-band test event, deleted order, missing
            # metadata. Don't 5xx — log and ack so Stripe stops retrying.
            logger.error(
                "Stripe webhook for unknown order: event_type=%s event_id=%s pi_id=%s",
                event_type, event_id, _pi_id(event),
            )
            return Response({"detail": "order not found"}, status=status.HTTP_200_OK)

        # Always upsert the local PaymentIntent record for visibility.
        try:
            record_payment_intent_event(order=order, stripe_event=event)
        except Exception:
            logger.exception("Failed to record PaymentIntent for event %s", event_id)
            # Continue — the transition is more important than the audit row.

        if event_type == "payment_intent.succeeded":
            try:
                transition_to_paid(order, stripe_event=event)
            except InvalidTransition as e:
                if order.status == "paid":
                    # Idempotent replay — Stripe retried a delivery we already
                    # processed. Not an error.
                    logger.info(
                        "Stripe webhook replay for already-paid order %s (event %s)",
                        order.pk, event_id,
                    )
                else:
                    # Genuine state mismatch (e.g. the order was cancelled
                    # before payment landed). Log and ack — manual reconciliation.
                    logger.error(
                        "Stripe succeeded event for order %s in unexpected status %r: %s",
                        order.pk, order.status, e,
                    )

        return Response({"detail": "ok"}, status=status.HTTP_200_OK)


def _pi_id(event) -> str:
    try:
        return event["data"]["object"]["id"]
    except (KeyError, TypeError):
        return ""


def _find_order_for_event(event):
    """Locate the local Order for a Stripe PaymentIntent event.

    Prefer metadata.order_uuid (set by the checkout endpoint when it calls
    StripeService.create_payment_intent). Fall back to looking up via
    Order.stripe_payment_intent_id which the same flow denormalizes.
    """
    pi_object = event.get("data", {}).get("object", {}) or {}
    metadata = pi_object.get("metadata") or {}

    order_uuid = metadata.get("order_uuid")
    if order_uuid:
        try:
            return Order.objects.get(pk=order_uuid)
        except Order.DoesNotExist:
            pass

    pi_id = pi_object.get("id", "")
    if pi_id:
        return Order.objects.filter(stripe_payment_intent_id=pi_id).first()
    return None
