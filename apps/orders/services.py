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

from django.core.mail import send_mail
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

# Spanish IVA (VAT). Applied AFTER the addon stack + the MIN_TOTAL floor,
# so the floor is "minimum work value" (pre-IVA). Customer's displayed
# total INCLUDES IVA — Real Decreto Legislativo 1/2007 art. 60 requires
# B2C prices in Spain to be shown all-in. The summary card breaks out
# the IVA portion separately for transparency / invoicing.
IVA_RATE = Decimal("0.21")

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
    discount_percent: int = 0,
) -> int:
    """Pure pricing function. Decimal-based math, integer cents at the boundary.

    Validates the same constraints place_order enforces: known material,
    width/height multiples of 5 mm and >= 25 mm, quantity in [20, 100000].

    shipping_method stacks as another additive multiplier alongside the
    add-on surcharges: normal +0%, express +20%, flash +60%. Default
    'normal' so existing callers that don't pass it are unchanged.
    Unknown methods raise InvalidPricingInput.

    discount_percent (0-100, default 0): a promo-code discount applied
    AFTER the €20 floor and BEFORE the 21% IVA. The shop owner manages
    codes via apps.discounts.models.Discount; OrderViewSet.apply_discount
    stamps the percent onto the order at apply time and re-validates
    at place / checkout / reserve. The floor exists because the print
    shop has to cover materials + setup regardless; a discount on a
    €20-floor order still pays €20 × (1 - pct/100), plus IVA on that.
    """
    return _compute_breakdown(
        material=material,
        width_mm=width_mm,
        height_mm=height_mm,
        quantity=quantity,
        with_relief=with_relief,
        with_tinta_blanca=with_tinta_blanca,
        with_barniz_brillo=with_barniz_brillo,
        with_barniz_opaco=with_barniz_opaco,
        shipping_method=shipping_method,
        discount_percent=discount_percent,
    )["total_with_iva_cents"]


def _compute_breakdown(
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
    discount_percent: int = 0,
) -> dict:
    """Internal: returns the breakdown components so callers that need
    discount_cents alongside the all-in total don't have to compute
    twice. Always returns integer cents. Public callers should use
    compute_total_cents (just returns the all-in number).
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
    if not (0 <= discount_percent <= 100):
        raise InvalidPricingInput(
            f"discount_percent={discount_percent} outside [0, 100]"
        )

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

    # Pre-discount, pre-IVA subtotal: work × addon multipliers, floored
    # at the minimum-order value (the floor is on the WORK, not on the
    # all-in price — a small order pays €20 of work + IVA on top).
    pre_discount_cents = subtotal_cents * multiplier
    pre_discount_int = int(
        pre_discount_cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    pre_discount_int = max(pre_discount_int, MIN_TOTAL_CENTS)

    # Discount applied AFTER the floor, BEFORE IVA. So a 50% discount
    # on a €20-floor order pays €10 + 21% IVA = €12.10 customer-facing.
    discount_cents = int(
        (Decimal(pre_discount_int) * Decimal(discount_percent) / Decimal(100))
        .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    pre_iva_int = pre_discount_int - discount_cents

    # IVA applied on top of the discounted pre-IVA — customer total is
    # all-in (Spanish B2C convention). UI breaks out the IVA portion
    # via subtotal_cents_of() / iva_cents_of() helpers below.
    total_with_iva = Decimal(pre_iva_int) * (Decimal("1") + IVA_RATE)
    total_int = int(total_with_iva.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    return {
        "pre_discount_cents": pre_discount_int,
        "discount_cents": discount_cents,
        "pre_iva_cents": pre_iva_int,
        "total_with_iva_cents": total_int,
    }


def subtotal_cents_of(total_cents: int) -> int:
    """Reverse the IVA inclusion: total / (1 + IVA_RATE), rounded to cents.

    Used by the OrderSerializer to expose the pre-IVA subtotal alongside
    total_amount_cents — the customer-facing summary card breaks the
    line out as "Subtotal + IVA = Total". Round-trips exactly with
    compute_total_cents because both use ROUND_HALF_UP.
    """
    if total_cents <= 0:
        return 0
    sub = Decimal(total_cents) / (Decimal("1") + IVA_RATE)
    return int(sub.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def iva_cents_of(total_cents: int) -> int:
    """The IVA portion of an IVA-included total. Equal to total - subtotal."""
    return total_cents - subtotal_cents_of(total_cents)


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
    """effective_price_cents × product_quantity, minus discount, plus 21% IVA.

    Uses Product.effective_price_cents so a non-null sale_price_cents
    supersedes the regular price_cents. Discount (if any) is applied
    BEFORE IVA, matching the sticker pricing model — promo codes
    discount the work, then IVA rides on top of the discounted amount.

    Spanish B2C convention adds 21% IVA to the customer-facing total;
    UI breaks out the IVA portion via subtotal_cents_of / iva_cents_of.
    """
    unit_pre_iva = order.product.effective_price_cents
    pre_discount = Decimal(unit_pre_iva * order.product_quantity)
    # Catalog has no €20 floor (each product has its own price; small
    # items like a €3 keychain shouldn't be inflated to €20 just to
    # match the sticker floor). Discount applies straight to the
    # pre-discount work amount.
    discount_pct = _discount_percent_for_order(order)
    discount = (pre_discount * Decimal(discount_pct) / Decimal(100)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    pre_iva = pre_discount - discount
    total = pre_iva * (Decimal("1") + IVA_RATE)
    return int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def apply_discount_to_order(order: Order, *, code: str, actor) -> Order:
    """Customer applies a promo code to their draft order.

    Looks up the code (case-insensitive, normalized to upper inside
    Discount.save). Raises InvalidTransition / InvalidPricingInput on
    failure modes the view translates to specific HTTP codes:

      - InvalidPricingInput('not_found') → 404 (no such code)
      - InvalidTransition('disabled')     → 409 (code exists but off)
      - InvalidTransition('wrong_status') → 409 (order isn't a draft)

    On success: stamps order.discount_code (uppercase) +
    order.discount_cents, recomputes total_amount_cents, saves, and
    returns the refreshed order. The recompute uses the same
    _recompute_order_total path the place / reserve transitions use,
    so the customer-facing total reflects the discount immediately.
    """
    from apps.discounts.models import Discount

    _require_owner(order, actor)
    normalized = (code or "").strip().upper()
    if not normalized:
        raise InvalidPricingInput("not_found")
    try:
        discount = Discount.objects.get(code=normalized)
    except Discount.DoesNotExist:
        raise InvalidPricingInput("not_found")
    if not discount.is_enabled:
        raise InvalidTransition("disabled")

    with transaction.atomic():
        order = _lock(order)
        # Only drafts can have their discount changed. Once the order
        # is placed/paid/etc., the discount is part of the snapshot.
        if order.status != "draft":
            raise InvalidTransition("wrong_status")

        order.discount_code = normalized
        _recompute_order_total(order)
        order._history_user = actor
        order.save(
            update_fields=[
                "discount_code",
                "discount_cents",
                "total_amount_cents",
                "updated_at",
            ]
        )
        return order


def _recompute_order_total(order: Order) -> None:
    """Compute order.total_amount_cents and order.discount_cents from
    the current spec + discount_code state. Mutates the order in
    place; caller is responsible for saving.

    Single source of truth for the pricing math so place_order,
    reserve_order, and apply_discount all produce identical numbers.
    Skips empty/insufficient state (e.g. before the customer has
    picked a material) — those paths run their own fill-validation
    first.
    """
    discount_pct = _discount_percent_for_order(order)
    if order.kind == KIND_STICKER:
        breakdown = _compute_breakdown(
            material=order.material,
            width_mm=order.width_mm,
            height_mm=order.height_mm,
            quantity=order.quantity,
            with_relief=order.with_relief,
            with_tinta_blanca=order.with_tinta_blanca,
            with_barniz_brillo=order.with_barniz_brillo,
            with_barniz_opaco=order.with_barniz_opaco,
            shipping_method=order.shipping_method,
            discount_percent=discount_pct,
        )
        order.total_amount_cents = breakdown["total_with_iva_cents"]
        order.discount_cents = breakdown["discount_cents"]
    else:
        # Catalog: discount applies to product price × qty, before IVA.
        # _compute_catalog_total_cents reads the order's discount_code
        # to derive the percent, then folds discount + IVA in.
        unit_pre_iva = order.product.effective_price_cents
        pre_discount = Decimal(unit_pre_iva * order.product_quantity)
        discount = (
            pre_discount * Decimal(discount_pct) / Decimal(100)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        order.discount_cents = int(discount)
        order.total_amount_cents = _compute_catalog_total_cents(order)


def _discount_percent_for_order(order: Order) -> int:
    """Resolve the live discount percent for this order from its
    persisted discount_code. Returns 0 when there's no code OR when
    the code has been disabled / deleted since it was applied.

    Stored discount_cents is the snapshot from the apply moment, but
    we re-derive the percent here so the recomputed total can't drift
    if the admin disables the code between apply and place/checkout.
    """
    if not order.discount_code:
        return 0
    # Late import — apps.discounts imports apps.orders for the order
    # views' apply-discount action, so we'd hit a circular at module
    # load if we imported eagerly.
    from apps.discounts.models import Discount

    try:
        d = Discount.objects.get(code=order.discount_code)
    except Discount.DoesNotExist:
        return 0
    if not d.is_enabled:
        return 0
    return int(d.percent_off)


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

        for field in (
            "recipient_name",
            "street_line_1",
            "city",
            "postal_code",
            "country",
            "shipping_phone",
        ):
            if not getattr(order, field):
                missing.append(field)
        if missing:
            raise InvalidTransition(f"Cannot place order; missing: {', '.join(missing)}.")

        # Recompute total + discount_cents from scratch — picks up any
        # discount_code applied earlier via the /apply-discount/ endpoint.
        # If the admin disabled the code between apply and place, this
        # path produces a 0% discount silently (matches what the customer
        # would have seen if they'd re-validated; place_order doesn't
        # raise — they explicitly chose to checkout, the system shouldn't
        # block on an admin action).
        _recompute_order_total(order)

        order.status = "placed"
        order.placed_at = timezone.now()
        order.save(
            update_fields=[
                "status",
                "placed_at",
                "total_amount_cents",
                "discount_cents",
                "updated_at",
            ]
        )
        return order


def reserve_order(order: Order, *, actor, pickup_at) -> Order:
    """{draft, placed} → reserved. Whitelist-gated alternative to Stripe.

    The customer commits to picking up + paying cash in-store at
    `pickup_at`. Mirrors place_order's fill-validation so the order is
    production-ready by the time the owner takes payment.

    Caller must verify `actor.can_reserve_orders` before invoking — the
    view enforces this; this function trusts its caller.
    """
    from datetime import datetime as _datetime, timezone as _dt_timezone

    _require_owner(order, actor)

    # Accept ISO datetime strings (from JSON body) or pre-parsed datetime
    # objects (from internal callers / tests). Naive strings are
    # interpreted as UTC to match Django's USE_TZ=True convention.
    if isinstance(pickup_at, str):
        parsed = _datetime.fromisoformat(pickup_at.replace("Z", "+00:00"))
    elif isinstance(pickup_at, _datetime):
        parsed = pickup_at
    else:
        raise InvalidPricingInput(f"pickup_at must be a datetime, got {type(pickup_at)!r}")

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, _dt_timezone.utc)

    if parsed <= timezone.now():
        raise InvalidPricingInput("pickup_at must be in the future.")

    with transaction.atomic():
        order = _lock(order)
        if order.status not in {"draft", "placed"}:
            raise InvalidTransition(
                f"Cannot reserve order in status {order.status!r}."
            )

        # Same fill-validation as place_order — a reserved order still
        # needs the spec and shipping fields so production can start.
        if order.kind == KIND_STICKER:
            missing = _validate_sticker_required(order)
        elif order.kind == KIND_CATALOG:
            missing = _validate_catalog_required(order)
        else:
            raise InvalidTransition(f"Unknown order kind {order.kind!r}.")

        for field in (
            "recipient_name",
            "street_line_1",
            "city",
            "postal_code",
            "country",
            "shipping_phone",
        ):
            if not getattr(order, field):
                missing.append(field)
        if missing:
            raise InvalidTransition(
                f"Cannot reserve order; missing: {', '.join(missing)}."
            )

        # Compute the total now too — owner takes cash for this amount.
        # Picks up any discount the customer applied earlier; same
        # silent-fallback-to-0% behavior as place_order if the code
        # was disabled between apply and reserve.
        _recompute_order_total(order)

        order.status = "reserved"
        order.pickup_at = parsed
        now = timezone.now()
        order.reserved_at = now
        if order.placed_at is None:
            order.placed_at = now
        order._history_user = actor
        order.save(
            update_fields=[
                "status",
                "pickup_at",
                "reserved_at",
                "placed_at",
                "total_amount_cents",
                "discount_cents",
                "updated_at",
            ]
        )

    # Notification emails — reservation path. Same shape as the paid
    # path: customer gets a confirmation (with pickup_at), owner gets
    # a heads-up so they can prep the bag + take cash on the day.
    # Outside the transaction block so SMTP latency doesn't hold the
    # row lock.
    _send_order_received_to_customer(order)
    _send_new_order_to_owner(order)

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

    # Notification emails — paid path. Customer gets a confirmation;
    # owner gets a heads-up. Both swallow their own SMTP errors so
    # this never blocks an otherwise-successful paid transition.
    _send_order_received_to_customer(order)
    _send_new_order_to_owner(order)

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


_STATUS_TIMESTAMP_FIELD = {
    "placed": "placed_at",
    "reserved": "reserved_at",
    "paid": "paid_at",
    "shipped": "shipped_at",
    "delivered": "delivered_at",
    "cancelled": "cancelled_at",
}


def admin_set_order_status(
    order: Order,
    *,
    new_status: str,
    actor,
    shipping_carrier: str = "",
    shipping_tracking_code: str = "",
    shipping_eta_date: str | None = None,
) -> Order:
    """Manual status override for staff. Bypasses transition guards.

    Side effects:
      - Sets the matching *_at timestamp if not already populated and
        the status implies one. Doesn't clear timestamps when status
        moves backwards (so a re-opened order still records the prior
        shipped_at — useful audit trail).
      - When new_status == 'shipped' and carrier + tracking are provided,
        persists them on the order and sends the customer a notification
        email via _send_shipping_notification.

    Doesn't recompute totals, file uploads, stock, or anything else — the
    caller is the shop owner, who's expected to know what they're doing.
    """
    _require_staff(actor)
    from datetime import date as _date

    parsed_eta: _date | None = None
    if shipping_eta_date:
        if isinstance(shipping_eta_date, _date):
            parsed_eta = shipping_eta_date
        else:
            # Accept "YYYY-MM-DD" (HTML date input format).
            parsed_eta = _date.fromisoformat(str(shipping_eta_date))

    with transaction.atomic():
        order = _lock(order)
        order.status = new_status
        ts_field = _STATUS_TIMESTAMP_FIELD.get(new_status)
        update_fields = ["status", "updated_at"]
        if ts_field and getattr(order, ts_field) is None:
            setattr(order, ts_field, timezone.now())
            update_fields.append(ts_field)

        notify = False
        if new_status == "shipped":
            if shipping_carrier:
                order.shipping_carrier = shipping_carrier
                update_fields.append("shipping_carrier")
            if shipping_tracking_code:
                order.shipping_tracking_code = shipping_tracking_code
                update_fields.append("shipping_tracking_code")
            if parsed_eta is not None:
                order.shipping_eta_date = parsed_eta
                update_fields.append("shipping_eta_date")
            # Email is worth sending only if the customer has something
            # to act on — at least a tracking code.
            notify = bool(order.shipping_tracking_code)

        order._history_user = actor
        order.save(update_fields=update_fields)

        if notify:
            _send_shipping_notification(order)
        return order


def _send_shipping_notification(order: Order) -> None:
    """Plain-text email to the customer with carrier + tracking + ETA.

    Synchronous send; uses Django's default email backend. Failures are
    logged but never bubble up — losing an email shouldn't block the
    shop owner from marking the order shipped.
    """
    from django.conf import settings
    import logging

    logger = logging.getLogger(__name__)

    recipient = order.shipping_email or (
        order.created_by.email if order.created_by_id else ""
    )
    if not recipient:
        logger.warning(
            "Shipping notification skipped for order %s — no recipient email.",
            order.uuid,
        )
        return

    short_uuid = str(order.uuid)[:8]
    eta_line = ""
    if order.shipping_eta_date:
        eta_line = (
            f"Fecha estimada de entrega: {order.shipping_eta_date.isoformat()}\n"
        )

    body = (
        f"¡Hola!\n\n"
        f"Tu pedido #{short_uuid} ya está en camino.\n\n"
        f"Transportista: {order.shipping_carrier or '—'}\n"
        f"Código de seguimiento: {order.shipping_tracking_code or '—'}\n"
        f"{eta_line}"
        f"\nGracias por confiar en nuestro taller.\n"
    )

    try:
        send_mail(
            subject=f"Tu pedido #{short_uuid} está en camino",
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@stickerapp.local"),
            recipient_list=[recipient],
            fail_silently=False,
        )
    except Exception as exc:  # noqa: BLE001 — third-party / SMTP errors
        logger.warning(
            "Shipping notification email failed for order %s: %s",
            order.uuid,
            exc,
        )


def _send_order_received_to_customer(order: Order) -> None:
    """Confirmation email to the customer after their order is committed.

    Triggers from two paths:
      - transition_to_paid (Stripe webhook): "received + paid online"
      - reserve_order: "received + reserved for in-store pickup"

    Both render with the same template but branch the body on
    order.status so the reservation copy mentions the pickup date.
    Synchronous send; SMTP failures are logged but never raised.
    """
    from django.conf import settings
    import logging

    logger = logging.getLogger(__name__)

    recipient = order.shipping_email or (
        order.created_by.email if order.created_by_id else ""
    )
    if not recipient:
        logger.warning(
            "Order-received email skipped for order %s — no recipient email.",
            order.uuid,
        )
        return

    short_uuid = str(order.uuid)[:8]
    is_reserved = order.status == "reserved"

    if is_reserved:
        pickup_line = ""
        if order.pickup_at:
            pickup_line = (
                f"Fecha de retiro: {order.pickup_at.strftime('%d/%m/%Y %H:%M')}\n"
                f"Pago: en efectivo, al retirar\n\n"
            )
        body = (
            f"¡Hola!\n\n"
            f"Recibimos tu reserva #{short_uuid}.\n\n"
            f"{pickup_line}"
            f"Te esperamos en la tienda. Si necesitás reprogramar, "
            f"escribinos a {settings.SHOP_OWNER_EMAIL}.\n\n"
            f"Gracias por elegir nuestro taller.\n"
        )
        subject = f"Reservamos tu pedido #{short_uuid}"
    else:
        body = (
            f"¡Hola!\n\n"
            f"Recibimos tu pedido #{short_uuid} y ya está confirmado.\n\n"
            f"Total pagado: €{order.total_eur if hasattr(order, 'total_eur') else (order.total_amount_cents / 100):.2f}\n\n"
            f"Te avisaremos por email cuando entre en producción y de nuevo cuando lo enviemos.\n\n"
            f"Gracias por elegir nuestro taller.\n"
        )
        subject = f"Recibimos tu pedido #{short_uuid}"

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@stickerapp.local"),
            recipient_list=[recipient],
            fail_silently=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Order-received email failed for order %s: %s",
            order.uuid,
            exc,
        )


def _send_new_order_to_owner(order: Order) -> None:
    """Notify the shop owner that a new order landed.

    Triggers from the same paths as the customer email
    (transition_to_paid + reserve_order). Sends to settings
    .SHOP_OWNER_EMAIL with enough detail that the owner doesn't
    need to open the admin to know what arrived: order ID, customer
    name, total, kind (sticker / catalog), and pickup info when
    relevant.
    """
    from django.conf import settings
    import logging

    logger = logging.getLogger(__name__)

    recipient = getattr(settings, "SHOP_OWNER_EMAIL", "")
    if not recipient:
        logger.warning(
            "Owner notification skipped for order %s — SHOP_OWNER_EMAIL not configured.",
            order.uuid,
        )
        return

    short_uuid = str(order.uuid)[:8]
    is_reserved = order.status == "reserved"
    customer_name = (
        order.recipient_name
        or (order.created_by.get_full_name() if order.created_by_id else "")
        or "—"
    )
    customer_email = (
        order.created_by.email if order.created_by_id else order.shipping_email
    ) or "—"
    kind_label = "Catálogo" if order.kind == "catalog" else "Sticker"
    total_eur = order.total_amount_cents / 100

    if is_reserved:
        pickup_line = ""
        if order.pickup_at:
            pickup_line = (
                f"Retiro: {order.pickup_at.strftime('%d/%m/%Y %H:%M')} "
                f"(en efectivo, al retirar)\n"
            )
        body = (
            f"Nueva reserva #{short_uuid}\n\n"
            f"Cliente: {customer_name} <{customer_email}>\n"
            f"Tipo: {kind_label}\n"
            f"Total: €{total_eur:.2f}\n"
            f"{pickup_line}"
            f"\nGestioná en /admin/orders/{order.uuid}\n"
        )
        subject = f"[Reserva] #{short_uuid} — {customer_name}"
    else:
        body = (
            f"Nuevo pedido pagado #{short_uuid}\n\n"
            f"Cliente: {customer_name} <{customer_email}>\n"
            f"Tipo: {kind_label}\n"
            f"Total: €{total_eur:.2f}\n\n"
            f"Gestioná en /admin/orders/{order.uuid}\n"
        )
        subject = f"[Pedido pagado] #{short_uuid} — {customer_name}"

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@stickerapp.local"),
            recipient_list=[recipient],
            fail_silently=False,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Owner notification email failed for order %s: %s",
            order.uuid,
            exc,
        )
