"""
Order business logic.

Per CLAUDE.md: business logic lives in services.py, not in views or
serializers. When the Order model lands, things like:

  - place_order(customer, files, shipping) -> Order
  - transition_order_to_paid(order, stripe_payment_intent) -> Order
  - cancel_order(order, reason) -> Order

go here as plain functions (or a class if state needs grouping).
Keep them framework-agnostic where possible — easier to test.
"""
