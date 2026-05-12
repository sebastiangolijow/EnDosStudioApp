from rest_framework import serializers

from .models import Discount


class DiscountSerializer(serializers.ModelSerializer):
    """Read + write surface for the admin discounts CRUD.

    The admin form posts {code, percent_off, is_enabled}; everything
    else (uuid, timestamps) is read-only. Code is normalized to upper
    inside Discount.save().
    """

    class Meta:
        model = Discount
        fields = [
            "uuid",
            "code",
            "percent_off",
            "is_enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["uuid", "created_at", "updated_at"]
