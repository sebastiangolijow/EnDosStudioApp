"""Product DRF serializers.

Two serializers:
  - ProductSerializer: public read shape (uuid, slug, image URL, price_eur for display)
  - ProductWriteSerializer: admin create/update (multipart for image upload)

Slug is read-only — auto-generated server-side from name in Product.save().
"""
from rest_framework import serializers

from .models import Product


class ProductSerializer(serializers.ModelSerializer):
    price_eur = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "uuid",
            "name",
            "slug",
            "description",
            "price_cents",
            "price_eur",
            "stock_quantity",
            "image",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "uuid",
            "slug",
            "price_eur",
            "created_at",
            "updated_at",
        ]

    def get_price_eur(self, obj) -> str:
        return f"{obj.price_cents / 100:.2f}"


class ProductWriteSerializer(serializers.ModelSerializer):
    """Admin create/update. Multipart-friendly (image is optional file)."""

    class Meta:
        model = Product
        fields = [
            "name",
            "description",
            "price_cents",
            "stock_quantity",
            "image",
            "is_active",
        ]
