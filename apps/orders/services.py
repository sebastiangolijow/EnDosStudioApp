"""
Order business logic.

Per CLAUDE.md: business logic lives in services.py, not in views/serializers.

This module exposes:
  - Pricing constants (material price, bleed margin, add-on surcharges,
    minimum-order floor)
  - compute_total_cents() — pure function used by place_order and quote
    endpoints
  - 6 lifecycle transitions (place_order, transition_to_paid,
    transition_to_in_production, transition_to_shipped, mark_delivered,
    cancel_order). Each guards the source status; raises InvalidTransition
    on failure (views translate to 409 Conflict).

Pricing formula (locked with the client on 2026-05-09):

    area_factor      = ((width_mm + 15) / 1000) × ((height_mm + 15) / 1000)
                       # area in m², including a 15 mm bleed margin per side
    subtotal_eur     = area_factor × quantity × material_price_eur
    addon_multiplier = 1
                     + (0.35 if with_relief)
                     + (0.35 if with_tinta_blanca)
                     + (0.20 if with_barniz_brillo)
                     + (0.20 if with_barniz_opaco)
    total_eur        = max(subtotal_eur × addon_multiplier, 20.00)

Add-on surcharges compose ADDITIVELY (sum the percents); the 20€ floor
applies AFTER add-ons. All math runs in Decimal then casts to integer
cents at the boundary — no float arithmetic anywhere.

Sizing rules: width_mm and height_mm must be multiples of 5 (half-cm
allowed) and at least 25 mm (2.5 cm). Quantity must be in [20, 100000].
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from .models import (
    DIMENSION_STEP_MM,
    KIND_CATALOG,
    KIND_STICKER,
    MAX_QUANTITY,
    MIN_DIMENSION_MM,
    MIN_QUANTITY,
    Order,
)


# ---------------------------------------------------------------------------
# Pricing constants — confirmed with the client on 2026-05-09
# ---------------------------------------------------------------------------

# Material price (cents). Plugged into the area formula as an "€ per m² per
# sticker" rate. Same per-material numbers the customer-facing material picker
# shows (45€, 50€, 55€, 60€).
MATERIAL_PRICE_CENTS = {
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

# Bleed margin added to BOTH width and height before the area calculation.
# 15 mm matches the editor's documented bleed (CLAUDE.md §editor).
BLEED_MARGIN_MM = 15

# Minimum order total — anything below this gets bumped up.
MIN_TOTAL_CENTS = 2000  # 20.00 €

# Add-on surcharges — additive percent multipliers (1.0 = +100%).
RELIEF_SURCHARGE_PCT = Decimal("0.35")
TINTA_BLANCA_SURCHARGE_PCT = Decimal("0.35")
BARNIZ_BRILLO_SURCHARGE_PCT = Decimal("0.20")
BARNIZ_OPACO_SURCHARGE_PCT = Decimal("0.20")

# Shipping surcharges — same multiplicative stacking as the add-ons.
# Normal adds 0 (default speed). Express +20% (2-3 days). Flash +60%
# (next-day). Mutually exclusive by enum; only one applies.
SHIPPING_SURCHARGE_PCT = {
    "normal": Decimal("0.00"),
    "express": Decimal("0.20"),
    "flash": Decimal("0.60"),
}

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
    with_relief: bool = False,
    with_tinta_blanca: bool = False,
    with_barniz_brillo: bool = False,
    with_barniz_opaco: bool = False,
    shipping_method: str = "normal",
) -> int:
    """Pure pricing function. Decimal-based math, integer cents at the boundary.

    Validates the same constraints place_order enforces: known material,
    width/height multiples of 5 mm and >= 25 mm, quantity in [20, 100000].

    shipping_method stacks as another additive multiplier alongside the
    add-on surcharges: normal +0%, express +20%, flash +60%. Default
    'normal' so existing callers that don't pass it are unchanged.
    Unknown methods raise InvalidPricingInput.
    """
    if material not in MATERIAL_PRICE_CENTS:
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
    if shipping_method not in SHIPPING_SURCHARGE_PCT:
        raise InvalidPricingInput(f"Unknown shipping_method: {shipping_method!r}")

    # Area in m² including the 15 mm bleed margin on each side.
    bleed = Decimal(BLEED_MARGIN_MM)
    width_m = (Decimal(width_mm) + bleed) / Decimal(1000)
    height_m = (Decimal(height_mm) + bleed) / Decimal(1000)
    material_cents = Decimal(MATERIAL_PRICE_CENTS[material])

    subtotal_cents = width_m * height_m * Decimal(quantity) * material_cents

    multiplier = Decimal("1")
    if with_relief:
        multiplier += RELIEF_SURCHARGE_PCT
    if with_tinta_blanca:
        multiplier += TINTA_BLANCA_SURCHARGE_PCT
    if with_barniz_brillo:
        multiplier += BARNIZ_BRILLO_SURCHARGE_PCT
    if with_barniz_opaco:
        multiplier += BARNIZ_OPACO_SURCHARGE_PCT
    multiplier += SHIPPING_SURCHARGE_PCT[shipping_method]

    total_cents = subtotal_cents * multiplier
    total_cents_int = int(total_cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return max(total_cents_int, MIN_TOTAL_CENTS)


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

def _validate_sticker_required(order: Order) -> list[str]:
    """Field-level requirements for a sticker order at place_order time."""
    missing = []
    if not order.material:
        missing.append("material")
    if order.width_mm < MIN_DIMENSION_MM or order.width_mm % DIMENSION_STEP_MM != 0:
        missing.append("width_mm")
    if order.height_mm < MIN_DIMENSION_MM or order.height_mm % DIMENSION_STEP_MM != 0:
        missing.append("height_mm")
    if order.quantity < MIN_QUANTITY or order.quantity > MAX_QUANTITY:
        missing.append("quantity")
    if not order.files.filter(kind="original").exists():
        missing.append("file:original")
    return missing


def _validate_catalog_required(order: Order) -> list[str]:
    """Field-level requirements for a catalog order at place_order time.

    Includes an initial stock check; checkout re-checks just before Stripe
    PaymentIntent creation (race-safe path is in transition_to_paid).
    """
    missing = []
    if order.product_id is None:
        missing.append("product")
        return missing  # nothing else makes sense without a product
    if order.product_quantity < 1:
        missing.append("product_quantity")
    if not order.product.is_active:
        missing.append("product:inactive")
    if order.product.stock_quantity < order.product_quantity:
        missing.append("product:insufficient_stock")
    return missing


def _compute_catalog_total_cents(order: Order) -> int:
    """price_cents × product_quantity. Read price from the linked Product."""
    return order.product.price_cents * order.product_quantity


def place_order(order: Order) -> Order:
    """draft → placed. Validates required fields and computes the total.

    Branches on order.kind:
      - sticker: existing M2 spec/dimension/quantity/file checks; pricing
        via the area×qty×material formula.
      - catalog: product must be set, active, and have sufficient stock;
        pricing is price_cents × product_quantity.

    Common: shipping fields are always required.
    """
    with transaction.atomic():
        order = _lock(order)
        if order.status != "draft":
            raise InvalidTransition(f"Cannot place order in status {order.status!r}.")

        if order.kind == KIND_STICKER:
            missing = _validate_sticker_required(order)
        elif order.kind == KIND_CATALOG:
            missing = _validate_catalog_required(order)
        else:
            raise InvalidTransition(f"Unknown order kind {order.kind!r}.")

        for field in ("recipient_name", "street_line_1", "city", "postal_code", "country"):
            if not getattr(order, field):
                missing.append(field)
        if missing:
            raise InvalidTransition(f"Cannot place order; missing: {', '.join(missing)}.")

        if order.kind == KIND_STICKER:
            order.total_amount_cents = compute_total_cents(
                material=order.material,
                width_mm=order.width_mm,
                height_mm=order.height_mm,
                quantity=order.quantity,
                with_relief=order.with_relief,
                with_tinta_blanca=order.with_tinta_blanca,
                with_barniz_brillo=order.with_barniz_brillo,
                with_barniz_opaco=order.with_barniz_opaco,
                shipping_method=order.shipping_method,
            )
        else:
            order.total_amount_cents = _compute_catalog_total_cents(order)

        order.status = "placed"
        order.placed_at = timezone.now()
        order.save(update_fields=["status", "placed_at", "total_amount_cents", "updated_at"])
        return order


def transition_to_paid(order: Order, *, stripe_event: dict | None = None, actor=None) -> Order:
    """placed → paid.

    Triggered in two ways:
      1. Stripe webhook (system action, no actor) — pass `stripe_event`.
      2. Manual admin "mark as paid" (staff handles payment out-of-band,
         e.g. bank transfer / cash on pickup) — pass `actor=request.user`,
         leave stripe_event as None. The Order's history row records
         who flipped the switch.

    Side effects branch by kind:
      - sticker: generate the cut-path SVG so the printer has both the
        artwork and the cutter file ready by the time the order moves to
        in_production. Failure is logged but doesn't roll back the paid
        transition — the SVG can be regenerated later via admin.
      - catalog: lock the product row inside the transaction and
        decrement stock_quantity by product_quantity. Race-safe via
        select_for_update. If somehow under-stocked at this point (two
        simultaneous payments), log a warning and allow the oversell —
        the shop reconciles with whichever customer bought last.
    """
    with transaction.atomic():
        order = _lock(order)
        if order.status != "placed":
            raise InvalidTransition(f"Cannot mark paid from status {order.status!r}.")

        if order.kind == KIND_CATALOG:
            _decrement_product_stock(order)

        order.status = "paid"
        order.paid_at = timezone.now()
        # System action when called from Stripe webhook (actor=None);
        # human actor when called from the admin mark-paid endpoint.
        order._history_user = actor
        order.save(update_fields=["status", "paid_at", "updated_at"])

    # Sticker-only side effect, after the lock releases. File IO outside
    # the row lock keeps the transaction window short. Failure here
    # doesn't unwind the paid transition; the order is still paid, the
    # cut SVG just needs a manual re-run.
    if order.kind == KIND_STICKER:
        try:
            from .cut_path import generate_cut_path_file
            generate_cut_path_file(order)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception(
                "Failed to generate cut_path for order %s: %s", order.uuid, exc,
            )
    return order


def _decrement_product_stock(order: Order) -> None:
    """Catalog-side stock decrement. Must run inside an open transaction."""
    from apps.products.models import Product

    product = Product.objects.select_for_update().get(pk=order.product_id)
    if product.stock_quantity < order.product_quantity:
        import logging
        logging.getLogger(__name__).warning(
            "Oversell allowed: product %s stock=%d, order qty=%d (order %s)",
            product.pk, product.stock_quantity, order.product_quantity, order.uuid,
        )
    product.stock_quantity = max(0, product.stock_quantity - order.product_quantity)
    product.save(update_fields=["stock_quantity", "updated_at"])


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
