from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("email", "role", "is_active", "is_verified", "created_at")
    list_filter = ("role", "is_active", "is_verified")
    search_fields = ("email", "first_name", "last_name", "phone_number")
    ordering = ("-created_at",)

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("first_name", "last_name", "phone_number", "profile_picture", "language")}),
        ("Role & status", {"fields": ("role", "is_active", "is_verified", "is_staff", "is_superuser")}),
        ("Verification", {"fields": ("verification_token", "verification_token_created_at")}),
        ("Permissions", {"fields": ("groups", "user_permissions")}),
        ("Timestamps", {"fields": ("created_at", "updated_at", "deleted_at", "last_login")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "password1", "password2", "role"),
        }),
    )
    readonly_fields = ("created_at", "updated_at", "last_login")
