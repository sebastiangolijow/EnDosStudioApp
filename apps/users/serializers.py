from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = User
        fields = [
            "uuid",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "phone_number",
            "profile_picture",
            "language",
            "role",
            "is_active",
            "is_verified",
            "can_reserve_orders",
            "created_at",
        ]
        read_only_fields = [
            "uuid",
            "role",
            "is_active",
            "is_verified",
            "can_reserve_orders",
            "created_at",
        ]


class AdminUserWriteSerializer(serializers.ModelSerializer):
    """Staff-only PATCH. Whitelisted fields are the only thing the
    shop owner edits from the admin users page — currently just the
    `can_reserve_orders` flag. Add fields here as the admin surface
    grows (e.g. flipping `is_active` to ban an account)."""

    class Meta:
        model = User
        fields = ["can_reserve_orders"]


class RegisterSerializer(serializers.Serializer):
    """Customer self-registration. Always creates role=customer, is_active=False.

    Phone number is required at the serializer layer (not the model
    layer). Existing User rows in the DB may have blank phones — making
    the column NOT NULL would require backfilling those, which we
    haven't done. Serializer-level enforcement means new signups must
    provide a phone while legacy accounts keep working unchanged.
    """

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(max_length=50, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=50, required=False, allow_blank=True)
    phone_number = serializers.CharField(max_length=50)


class SetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)
