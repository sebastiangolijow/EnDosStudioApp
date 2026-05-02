"""Order DRF serializers.

We split read/update because:
  - read returns ALL fields including computed/derived ones
  - update is PATCH-only and limited to fields editable while draft
Create takes nothing the customer provides (always role=customer,
status=draft, created_by=request.user — set in the view).
"""
from rest_framework import serializers

from .models import (
    DIMENSION_STEP_MM,
    MATERIAL_CHOICES,
    MAX_QUANTITY,
    MIN_DIMENSION_MM,
    MIN_QUANTITY,
    Order,
    OrderFile,
)


class OrderFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderFile
        fields = ["uuid", "kind", "file", "mime_type", "size_bytes", "created_at"]
        read_only_fields = ["uuid", "mime_type", "size_bytes", "created_at"]


class OrderSerializer(serializers.ModelSerializer):
    files = OrderFileSerializer(many=True, read_only=True)
    total_eur = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            "uuid",
            "status",
            # Sticker spec
            "material",
            "width_mm",
            "height_mm",
            "quantity",
            "with_design_service",
            "with_varnish",
            "with_relief",
            "relief_note",
            # Shipping
            "recipient_name",
            "street_line_1",
            "street_line_2",
            "city",
            "postal_code",
            "country",
            # Money
            "total_amount_cents",
            "total_eur",
            "currency",
            "stripe_payment_intent_id",
            # Files
            "files",
            # Lifecycle timestamps
            "created_at",
            "updated_at",
            "placed_at",
            "paid_at",
            "shipped_at",
            "delivered_at",
            "cancelled_at",
        ]
        read_only_fields = [
            "uuid",
            "status",
            "total_amount_cents",
            "total_eur",
            "currency",
            "stripe_payment_intent_id",
            "files",
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


class OrderUpdateSerializer(serializers.ModelSerializer):
    """PATCH serializer — only fields the customer edits while draft."""

    class Meta:
        model = Order
        fields = [
            "material",
            "width_mm",
            "height_mm",
            "quantity",
            "with_design_service",
            "with_varnish",
            "with_relief",
            "relief_note",
            "recipient_name",
            "street_line_1",
            "street_line_2",
            "city",
            "postal_code",
            "country",
        ]


class PriceQuoteSerializer(serializers.Serializer):
    """Inputs for GET /api/v1/orders/quote/. Mirrors compute_total_cents."""

    material = serializers.ChoiceField(choices=[c[0] for c in MATERIAL_CHOICES])
    width_mm = serializers.IntegerField(min_value=MIN_DIMENSION_MM)
    height_mm = serializers.IntegerField(min_value=MIN_DIMENSION_MM)
    quantity = serializers.IntegerField(min_value=MIN_QUANTITY, max_value=MAX_QUANTITY)
    with_design_service = serializers.BooleanField(required=False, default=False)
    with_varnish = serializers.BooleanField(required=False, default=False)
    with_relief = serializers.BooleanField(required=False, default=False)

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
