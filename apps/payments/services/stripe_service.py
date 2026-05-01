"""
Thin wrapper around the Stripe SDK.

Goal: every Stripe call in the app goes through this service, so when
we need to swap to Stripe Connect, change API versions, or add retry
logic, there's exactly one place to change it. Don't import `stripe`
directly from views or serializers.

The class is a deliberate facade over a small subset of the SDK; it
isn't a generic abstraction. We commit to Stripe.
"""
import logging

import stripe
from django.conf import settings

logger = logging.getLogger(__name__)


class StripeService:
    """Stripe SDK facade. Configure once at construction; methods stay focused."""

    def __init__(self, api_key: str | None = None):
        stripe.api_key = api_key or settings.STRIPE_SECRET_KEY

    def create_payment_intent(self, *, amount_cents: int, currency: str = "eur", **metadata):
        """
        Create a PaymentIntent. Returns the SDK object; the caller is
        responsible for persisting whatever local record they need.

        amount_cents is in the smallest currency unit (cents for EUR/USD).
        """
        return stripe.PaymentIntent.create(
            amount=amount_cents,
            currency=currency,
            automatic_payment_methods={"enabled": True},
            metadata=metadata or None,
        )

    def construct_webhook_event(self, payload: bytes, signature: str):
        """
        Verify + parse a webhook payload. Raises stripe.error.SignatureVerificationError
        if the signature doesn't match — let it propagate; the view returns 400.
        """
        return stripe.Webhook.construct_event(
            payload=payload,
            sig_header=signature,
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
