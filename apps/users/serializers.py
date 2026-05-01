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
            "created_at",
        ]
        read_only_fields = [
            "uuid",
            "role",
            "is_active",
            "is_verified",
            "created_at",
        ]


class RegisterSerializer(serializers.Serializer):
    """Customer self-registration. Always creates role=customer, is_active=False."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(max_length=50, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=50, required=False, allow_blank=True)


class SetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)
