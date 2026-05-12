"""Order DRF serializers.

We split read/update because:
  - read returns ALL fields including computed/derived ones
  - update is PATCH-only and limited to fields editable while draft
Create takes nothing the customer provides (always role=customer,
status=draft, created_by=request.user — set in the view).
"""
from rest_framework import serializers

from apps.products.models import Product

from .models import (
    DIMENSION_STEP_MM,
    KIND_CATALOG,
    KIND_STICKER,
    MATERIAL_CHOICES,
    MAX_QUANTITY,
    MIN_DIMENSION_MM,
    MIN_QUANTITY,
    Order,
    OrderFile,
    SHIPPING_METHOD_CHOICES,
)


class OrderFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderFile
        fields = ["uuid", "kind", "file", "mime_type", "size_bytes", "created_at"]
        read_only_fields = ["uuid", "mime_type", "size_bytes", "created_at"]


class ProductRefSerializer(serializers.ModelSerializer):
    """Tiny embed of a Product on Order.product_detail.

    Just enough for the frontend to render the catalog summary without a
    second fetch. Includes the discounted price so the strikethrough
    rendering on /checkout matches the catalog detail page.
    """

    price_eur = serializers.SerializerMethodField()
    sale_price_eur = serializers.SerializerMethodField()
    effective_price_cents = serializers.IntegerField(read_only=True)
    effective_price_eur = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "uuid",
            "name",
            "slug",
            "image",
            "price_cents",
            "price_eur",
            "sale_price_cents",
            "sale_price_eur",
            "effective_price_cents",
            "effective_price_eur",
        ]

    def get_price_eur(self, obj) -> str:
        return f"{obj.price_cents / 100:.2f}"

    def get_sale_price_eur(self, obj) -> str | None:
        if not obj.sale_price_cents:
            return None
        return f"{obj.sale_price_cents / 100:.2f}"

    def get_effective_price_eur(self, obj) -> str:
        return f"{obj.effective_price_cents / 100:.2f}"


class OrderSerializer(serializers.ModelSerializer):
    files = OrderFileSerializer(many=True, read_only=True)
    total_eur = serializers.SerializerMethodField()
    # IVA breakdown derived from the IVA-included total_amount_cents.
    # Customer-facing summary card shows "Subtotal + IVA = Total" and
    # the invoice needs the IVA portion itemized (Spanish law).
    subtotal_cents = serializers.SerializerMethodField()
    iva_cents = serializers.SerializerMethodField()
    subtotal_eur = serializers.SerializerMethodField()
    iva_eur = serializers.SerializerMethodField()
    discount_eur = serializers.SerializerMethodField()
    product_detail = ProductRefSerializer(source="product", read_only=True)
    # Customer contact info — exposed so the admin orders screen can
    # show "Pedido #abc · Sebastián Golijow · seba@example.com" at a
    # glance without an extra fetch. Customers see their own info, so
    # no privacy concern. SerializerMethodFields keep the fallbacks in
    # one place (email when name is empty; pk when both are null
    # because created_by got SET_NULL'd by a user delete).
    customer_email = serializers.SerializerMethodField()
    customer_name = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "uuid",
            "kind",
            "status",
            # Sticker spec (kind=sticker)
            "material",
            "shape",
            "width_mm",
            "height_mm",
            "quantity",
            "with_relief",
            "with_tinta_blanca",
            "with_barniz_brillo",
            "with_barniz_opaco",
            "relief_note",
            # Catalog (kind=catalog)
            "product",
            "product_quantity",
            "product_detail",
            # Shipping
            "recipient_name",
            "street_line_1",
            "street_line_2",
            "city",
            "postal_code",
            "country",
            "shipping_phone",
            "shipping_email",
            "shipping_method",
            "shipping_carrier",
            "shipping_tracking_code",
            "shipping_eta_date",
            # Customer
            "customer_email",
            "customer_name",
            # Money — total_amount_cents includes IVA (Spanish B2C all-in
            # prices). subtotal_cents + iva_cents break that out for the
            # summary card and invoicing.
            "total_amount_cents",
            "total_eur",
            "subtotal_cents",
            "subtotal_eur",
            "iva_cents",
            "iva_eur",
            # Promo code applied to this order. discount_code is the
            # uppercase text (audit trail); discount_cents is the
            # integer cents subtracted from the pre-IVA work amount.
            # discount_eur is the same value formatted for display.
            "discount_code",
            "discount_cents",
            "discount_eur",
            "currency",
            "stripe_payment_intent_id",
            # Files
            "files",
            # Lifecycle timestamps
            "created_at",
            "updated_at",
            "placed_at",
            "reserved_at",
            "paid_at",
            "shipped_at",
            "delivered_at",
            "cancelled_at",
            # In-store pickup reservation — when the customer plans
            # to come collect + pay cash.
            "pickup_at",
        ]
        read_only_fields = [
            "uuid",
            "status",
            "customer_email",
            "customer_name",
            "total_amount_cents",
            "total_eur",
            "subtotal_cents",
            "subtotal_eur",
            "iva_cents",
            "iva_eur",
            "discount_code",
            "discount_cents",
            "discount_eur",
            "currency",
            "stripe_payment_intent_id",
            "files",
            "product_detail",
            "created_at",
            "updated_at",
            "placed_at",
            "paid_at",
            "shipped_at",
            "delivered_at",
            "cancelled_at",
        ]

    def get_total_eur(self, obj) -> str:
        return f"{obj.total_amount_cents / 100:.2f}"

    def get_subtotal_cents(self, obj) -> int:
        """Pre-discount, pre-IVA subtotal — what the work would cost
        without the promo code. The customer-facing summary card
        reads as 'Subtotal − Descuento + IVA = Total', so this value
        is the WORK price before any discount was subtracted.

        Derived as (total / 1.21) + discount_cents so the four lines
        round-trip exactly with total_amount_cents."""
        from .services import subtotal_cents_of
        return subtotal_cents_of(obj.total_amount_cents) + obj.discount_cents

    def get_iva_cents(self, obj) -> int:
        from .services import iva_cents_of
        return iva_cents_of(obj.total_amount_cents)

    def get_subtotal_eur(self, obj) -> str:
        from .services import subtotal_cents_of
        return f"{(subtotal_cents_of(obj.total_amount_cents) + obj.discount_cents) / 100:.2f}"

    def get_iva_eur(self, obj) -> str:
        from .services import iva_cents_of
        return f"{iva_cents_of(obj.total_amount_cents) / 100:.2f}"

    def get_discount_eur(self, obj) -> str:
        return f"{obj.discount_cents / 100:.2f}"

    def get_customer_email(self, obj) -> str:
        return obj.created_by.email if obj.created_by else ""

    def get_customer_name(self, obj) -> str:
        if not obj.created_by:
            return ""
        # get_full_name strips whitespace; falls back to email local-part
        # so the UI always has SOMETHING to render.
        full = obj.created_by.get_full_name()
        if full:
            return full
        email = obj.created_by.email or ""
        return email.split("@")[0] if email else ""


class OrderUpdateSerializer(serializers.ModelSerializer):
    """PATCH serializer — only fields the customer edits while draft.

    Sticker spec fields are not required at the serializer level (a draft
    can be partial); place_order enforces them at the lifecycle boundary.
    Same for catalog fields. Order.clean() (XOR) runs as part of model
    validation when full_clean() is invoked.
    """

    class Meta:
        model = Order
        fields = [
            "kind",
            # Sticker spec
            "material",
            "shape",
            "width_mm",
            "height_mm",
            "quantity",
            "with_relief",
            "with_tinta_blanca",
            "with_barniz_brillo",
            "with_barniz_opaco",
            "relief_note",
            # Catalog
            "product",
            "product_quantity",
            # Shipping
            "recipient_name",
            "street_line_1",
            "street_line_2",
            "city",
            "postal_code",
            "country",
            "shipping_phone",
            "shipping_email",
            "shipping_method",
        ]
        extra_kwargs = {
            "kind": {"required": False},
            "material": {"required": False},
            "shape": {"required": False},
            "width_mm": {"required": False},
            "height_mm": {"required": False},
            "quantity": {"required": False},
            "product": {"required": False, "allow_null": True},
            "product_quantity": {"required": False},
            "shipping_phone": {"required": False},
            "shipping_email": {"required": False},
            "shipping_method": {"required": False},
        }

    def validate(self, attrs):
        """Run the model's clean() to enforce the kind XOR.

        Without this, a customer could PATCH a sticker order with a
        catalog product attached (or vice-versa) — clean() catches that.
        """
        instance = self.instance
        if instance is None:
            return attrs
        # Build a temp Order with patch applied; run full_clean for XOR.
        for field, value in attrs.items():
            setattr(instance, field, value)
        from django.core.exceptions import ValidationError as DjangoValidationError
        try:
            instance.clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict)
        return attrs


class OrderCreateSerializer(serializers.ModelSerializer):
    """POST serializer for creating drafts.

    Customer can either create an empty sticker draft (default kind, all
    fields blank) or a catalog draft with product + product_quantity set
    up front. Shipping is filled at checkout. clean() XOR is enforced
    via validate().
    """

    class Meta:
        model = Order
        fields = ["kind", "product", "product_quantity"]
        extra_kwargs = {
            "kind": {"required": False},
            "product": {"required": False, "allow_null": True},
            "product_quantity": {"required": False},
        }

    def validate(self, attrs):
        kind = attrs.get("kind", KIND_STICKER)
        if kind == KIND_CATALOG:
            if not attrs.get("product"):
                raise serializers.ValidationError({"product": "Required for catalog orders."})
            if attrs.get("product_quantity", 0) < 1:
                raise serializers.ValidationError(
                    {"product_quantity": "Must be >= 1 for catalog orders."}
                )
        else:
            # sticker — reject any product/quantity sneaked in
            if attrs.get("product"):
                raise serializers.ValidationError(
                    {"product": "Sticker orders must not reference a product."}
                )
        return attrs


class PriceQuoteSerializer(serializers.Serializer):
    """Inputs for GET /api/v1/orders/quote/. Mirrors compute_total_cents."""

    material = serializers.ChoiceField(choices=[c[0] for c in MATERIAL_CHOICES])
    width_mm = serializers.IntegerField(min_value=MIN_DIMENSION_MM)
    height_mm = serializers.IntegerField(min_value=MIN_DIMENSION_MM)
    quantity = serializers.IntegerField(min_value=MIN_QUANTITY, max_value=MAX_QUANTITY)
    with_relief = serializers.BooleanField(required=False, default=False)
    with_tinta_blanca = serializers.BooleanField(required=False, default=False)
    with_barniz_brillo = serializers.BooleanField(required=False, default=False)
    with_barniz_opaco = serializers.BooleanField(required=False, default=False)
    shipping_method = serializers.ChoiceField(
        choices=[c[0] for c in SHIPPING_METHOD_CHOICES],
        required=False,
        default="normal",
    )

    def validate_width_mm(self, value):
        if value % DIMENSION_STEP_MM != 0:
            raise serializers.ValidationError(
                f"width_mm must be a multiple of {DIMENSION_STEP_MM}"
            )
        return value

    def validate_height_mm(self, value):
        if value % DIMENSION_STEP_MM != 0:
            raise serializers.ValidationError(
                f"height_mm must be a multiple of {DIMENSION_STEP_MM}"
            )
        return value


class CheckoutResponseSerializer(serializers.Serializer):
    """Output of POST /api/v1/orders/{uuid}/checkout/."""

    client_secret = serializers.CharField()
    payment_intent_id = serializers.CharField()
    amount_cents = serializers.IntegerField()
    currency = serializers.CharField()
