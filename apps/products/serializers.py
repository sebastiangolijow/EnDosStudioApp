"""Product DRF serializers.

Two serializers:
  - ProductSerializer: public read shape (uuid, slug, image URL, price_eur for display)
  - ProductWriteSerializer: admin create/update (multipart for image upload).
    Accepts category as a free-text name; the serializer dedupes through
    Category by slugified name so admins type-and-go without an explicit
    category-create step.

Slug is read-only — auto-generated server-side from name in Product.save().
"""
from django.utils.text import slugify
from rest_framework import serializers

from .models import Category, Product


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["uuid", "name", "slug"]
        read_only_fields = ["uuid", "slug"]


class ProductSerializer(serializers.ModelSerializer):
    price_eur = serializers.SerializerMethodField()
    sale_price_eur = serializers.SerializerMethodField()
    effective_price_cents = serializers.IntegerField(read_only=True)
    effective_price_eur = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "uuid",
            "name",
            "slug",
            "description",
            "price_cents",
            "price_eur",
            "sale_price_cents",
            "sale_price_eur",
            "effective_price_cents",
            "effective_price_eur",
            "weight_grams",
            "category",
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
            "sale_price_eur",
            "effective_price_cents",
            "effective_price_eur",
            "category",
            "created_at",
            "updated_at",
        ]

    def get_price_eur(self, obj) -> str:
        return f"{obj.price_cents / 100:.2f}"

    def get_sale_price_eur(self, obj) -> str | None:
        if not obj.sale_price_cents:
            return None
        return f"{obj.sale_price_cents / 100:.2f}"

    def get_effective_price_eur(self, obj) -> str:
        return f"{obj.effective_price_cents / 100:.2f}"

    def get_category(self, obj) -> dict | None:
        if obj.category_id is None:
            return None
        return {
            "uuid": str(obj.category.uuid),
            "name": obj.category.name,
            "slug": obj.category.slug,
        }


class ProductWriteSerializer(serializers.ModelSerializer):
    """Admin create/update. Multipart-friendly (image is optional file).

    `category` accepts a free-text name. Empty string clears the category;
    a new name creates a Category row (dedup by slug). Existing names
    are matched case-insensitively against `Category.slug`.
    """

    category = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = Product
        fields = [
            "name",
            "description",
            "price_cents",
            "sale_price_cents",
            "stock_quantity",
            "weight_grams",
            "category",
            "image",
            "is_active",
        ]

    def _resolve_category(self, value: str | None):
        if value is None:
            return None  # field not provided at all
        if not value or not value.strip():
            return False  # explicit clear
        name = value.strip()
        slug = slugify(name) or "categoria"
        cat = Category.objects.filter(slug=slug).first()
        if cat is None:
            cat = Category.objects.create(name=name, slug=slug)
        return cat

    def create(self, validated_data):
        cat_input = validated_data.pop("category", None)
        category = self._resolve_category(cat_input)
        product = Product.objects.create(**validated_data)
        if category is False:
            product.category = None
        elif category is not None:
            product.category = category
        if category is not None:
            product.save(update_fields=["category"])
        return product

    def update(self, instance, validated_data):
        cat_input = validated_data.pop("category", None)
        category = self._resolve_category(cat_input)
        for k, v in validated_data.items():
            setattr(instance, k, v)
        if category is False:
            instance.category = None
        elif category is not None:
            instance.category = category
        instance.save()
        return instance
