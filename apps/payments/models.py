"""
Payment records.

Intentionally empty at bootstrap. The next session designs the model
that records each Stripe transaction. Likely shape:

  - PaymentIntent (FK Order, stripe_payment_intent_id, status, amount,
                   currency, customer_email, raw_event JSON, created_at)

Stripe is the source of truth; this table is a local mirror for
reporting + debugging webhook flows. Don't store card data here ever.
"""
