"""
Order business logic.

Per CLAUDE.md: business logic lives in services.py, not in views/serializers.

This module exposes:
  - Pricing constants (material base, per-cm, per-sticker, add-on fees)
  - compute_total_cents() — pure function used by place_order and quote endpoints
  - 6 lifecycle transitions (place_order, transition_to_paid,
    transition_to_in_production, transition_to_shipped, mark_delivered,
    cancel_order). Each guards the source status; raises InvalidTransition
    on failure (views translate to 409 Conflict).

Pricing formula (sanity-checked against the reference shop's order detail
for holográfico, 5×5 cm, q=50, no add-ons → 110€):

    total_eur = material_base
              + (width_cm + height_cm) × 1€
              + quantity × 1€
              + (8€ if with_varnish)
              + (8€ if with_design_service)
              + (12€ if with_relief)

Sizing rules: width_mm and height_mm must be multiples of 5 (half-cm
allowed) and at least 25 mm (2.5 cm). Quantity must be in [20, 100000].
"""
from django.db import transaction
from django.utils import timezone

from .models import (
    DIMENSION_STEP_MM,
    MAX_QUANTITY,
    MIN_DIMENSION_MM,
    MIN_QUANTITY,
    Order,
)


# ---------------------------------------------------------------------------
# Pricing constants — confirmed against the reference shop on 2026-05-02
# ---------------------------------------------------------------------------

# Material base price (cents). Independent of size/quantity.
MATERIAL_BASE_CENTS = {
    "vinilo_blanco": 4500,
    "vinilo_transparente": 4500,
    "holografico": 5000,
    "holografico_transparente": 5000,
    "plateado": 5000,
    "dorado": 5000,
    "luminiscente": 5500,
    "eggshell": 5500,
    "eggshell_holografico": 6000,
}

# €1 per cm of (width + height); stored in cents per millimeter so width_mm
# and height_mm don't need integer cm. (1 cm = 100 cents → 10 cents per mm.)
SIZE_RATE_CENTS_PER_MM = 10

# €1 per sticker.
QUANTITY_RATE_CENTS_PER_UNIT = 100

# Add-on flat fees (cents)
DESIGN_SERVICE_FEE_CENTS = 800   # "maquetación de archivos"
VARNISH_FEE_CENTS = 800          # "barniz"
RELIEF_FEE_CENTS = 1200          # "relieve"

ADMIN_ROLES = {"admin", "shop_staff"}


class InvalidTransition(Exception):
    """Raised when a status transition guard fails. Views map to 409 Conflict."""


class InvalidPricingInput(ValueError):
    """Raised by compute_total_cents on bad material/size/quantity input."""


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

def compute_total_cents(
    *,
    material: str,
    width_mm: int,
    height_mm: int,
    quantity: int,
    with_design_service: bool = False,
    with_varnish: bool = False,
    with_relief: bool = False,
) -> int:
    """Pure pricing function. All math in integer cents, no floats.

    Validates the same constraints place_order enforces: known material,
    width/height multiples of 5 mm and >= 25 mm, quantity in [20, 100000].
    """
    if material not in MATERIAL_BASE_CENTS:
        raise InvalidPricingInput(f"Unknown material: {material!r}")
    for label, value in (("width_mm", width_mm), ("height_mm", height_mm)):
        if value < MIN_DIMENSION_MM:
            raise InvalidPricingInput(
                f"{label}={value} below minimum {MIN_DIMENSION_MM} mm"
            )
        if value % DIMENSION_STEP_MM != 0:
            raise InvalidPricingInput(
                f"{label}={value} must be a multiple of {DIMENSION_STEP_MM} mm"
            )
    if quantity < MIN_QUANTITY or quantity > MAX_QUANTITY:
        raise InvalidPricingInput(
            f"quantity={quantity} outside allowed range "
            f"[{MIN_QUANTITY}, {MAX_QUANTITY}]"
        )

    total = MATERIAL_BASE_CENTS[material]
    total += (width_mm + height_mm) * SIZE_RATE_CENTS_PER_MM
    total += quantity * QUANTITY_RATE_CENTS_PER_UNIT
    if with_design_service:
        total += DESIGN_SERVICE_FEE_CENTS
    if with_varnish:
        total += VARNISH_FEE_CENTS
    if with_relief:
        total += RELIEF_FEE_CENTS
    return total


# ---------------------------------------------------------------------------
# Transition helpers
# ---------------------------------------------------------------------------

def _lock(order: Order) -> Order:
    """Re-fetch the order with a row lock. Call inside a transaction."""
    return Order.objects.select_for_update().get(pk=order.pk)


def _require_owner(order: Order, actor) -> None:
    if actor is None or actor.pk != order.created_by_id:
        raise InvalidTransition("Only the order owner can perform this transition.")


def _require_staff(actor) -> None:
    if actor is None or actor.role not in ADMIN_ROLES:
        raise InvalidTransition("Only admin/shop_staff can perform this transition.")


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

def place_order(order: Order) -> Order:
    """draft → placed. Validates required fields, enforces size/quantity rules,
    computes the total."""
    with transaction.atomic():
        order = _lock(order)
        if order.status != "draft":
            raise InvalidTransition(f"Cannot place order in status {order.status!r}.")

        missing = []
        if not order.material:
            missing.append("material")
        if order.width_mm < MIN_DIMENSION_MM or order.width_mm % DIMENSION_STEP_MM != 0:
            missing.append("width_mm")
        if order.height_mm < MIN_DIMENSION_MM or order.height_mm % DIMENSION_STEP_MM != 0:
            missing.append("height_mm")
        if order.quantity < MIN_QUANTITY or order.quantity > MAX_QUANTITY:
            missing.append("quantity")
        for field in ("recipient_name", "street_line_1", "city", "postal_code", "country"):
            if not getattr(order, field):
                missing.append(field)
        if not order.files.filter(kind="original").exists():
            missing.append("file:original")
        if missing:
            raise InvalidTransition(f"Cannot place order; missing: {', '.join(missing)}.")

        order.total_amount_cents = compute_total_cents(
            material=order.material,
            width_mm=order.width_mm,
            height_mm=order.height_mm,
            quantity=order.quantity,
            with_design_service=order.with_design_service,
            with_varnish=order.with_varnish,
            with_relief=order.with_relief,
        )
        order.status = "placed"
        order.placed_at = timezone.now()
        order.save(update_fields=["status", "placed_at", "total_amount_cents", "updated_at"])
        return order


def transition_to_paid(order: Order, *, stripe_event: dict) -> Order:
    """placed → paid. System action (Stripe webhook); no actor."""
    with transaction.atomic():
        order = _lock(order)
        if order.status != "placed":
            raise InvalidTransition(f"Cannot mark paid from status {order.status!r}.")
        order.status = "paid"
        order.paid_at = timezone.now()
        order._history_user = None  # system action, no human actor
        order.save(update_fields=["status", "paid_at", "updated_at"])
        return order


def transition_to_in_production(order: Order, *, actor) -> Order:
    """paid → in_production. admin/shop_staff only."""
    _require_staff(actor)
    with transaction.atomic():
        order = _lock(order)
        if order.status != "paid":
            raise InvalidTransition(f"Cannot start production from status {order.status!r}.")
        order.status = "in_production"
        order._history_user = actor
        order.save(update_fields=["status", "updated_at"])
        return order


def transition_to_shipped(order: Order, *, actor) -> Order:
    """in_production → shipped. admin/shop_staff only."""
    _require_staff(actor)
    with transaction.atomic():
        order = _lock(order)
        if order.status != "in_production":
            raise InvalidTransition(f"Cannot ship from status {order.status!r}.")
        order.status = "shipped"
        order.shipped_at = timezone.now()
        order._history_user = actor
        order.save(update_fields=["status", "shipped_at", "updated_at"])
        return order


def mark_delivered(order: Order, *, actor) -> Order:
    """shipped → delivered. customer (owner) only."""
    _require_owner(order, actor)
    with transaction.atomic():
        order = _lock(order)
        if order.status != "shipped":
            raise InvalidTransition(f"Cannot mark delivered from status {order.status!r}.")
        order.status = "delivered"
        order.delivered_at = timezone.now()
        order._history_user = actor
        order.save(update_fields=["status", "delivered_at", "updated_at"])
        return order


def cancel_order(order: Order, *, actor, reason: str = "") -> Order:
    """{draft, placed} → cancelled. customer (owner) only.

    Cannot cancel after paid in M2 — refunds are out of scope. Customer
    must contact the shop; admin handles refunds via Stripe dashboard.
    """
    _require_owner(order, actor)
    with transaction.atomic():
        order = _lock(order)
        if order.status not in {"draft", "placed"}:
            raise InvalidTransition(
                f"Cannot cancel from status {order.status!r}; contact the shop for a refund."
            )
        order.status = "cancelled"
        order.cancelled_at = timezone.now()
        order._history_user = actor
        order.save(update_fields=["status", "cancelled_at", "updated_at"])
        return order
