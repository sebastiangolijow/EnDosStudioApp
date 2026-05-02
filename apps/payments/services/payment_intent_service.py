"""
Local PaymentIntent record management.

Stripe is the source of truth; this is the local mirror used for
reporting and webhook debugging. The Stripe webhook router (TODO,
landing in a follow-up substep) calls record_payment_intent_event()
for every PI event we care about.
"""
from apps.payments.models import PaymentIntent


def record_payment_intent_event(*, order, stripe_event: dict) -> PaymentIntent:
    """Upsert a PaymentIntent row from a Stripe webhook event.

    Looked up by stripe_payment_intent_id. raw_event is overwritten with
    the latest payload — we keep one row per Stripe PI, not one per event.

    Args:
        order: the local Order this PI belongs to.
        stripe_event: the parsed Stripe event dict (output of
                      StripeService.construct_webhook_event).

    Returns the persisted PaymentIntent row.
    """
    pi_data = stripe_event["data"]["object"]
    pi, _ = PaymentIntent.objects.update_or_create(
        stripe_payment_intent_id=pi_data["id"],
        defaults={
            "order": order,
            "status": pi_data.get("status", ""),
            "amount_cents": pi_data.get("amount", 0),
            "currency": (pi_data.get("currency") or "EUR").upper()[:3],
            "raw_event": stripe_event,
        },
    )
    return pi
