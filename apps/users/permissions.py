from rest_framework import permissions


class IsAdmin(permissions.BasePermission):
    """Admin-only access."""

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role == "admin"
        )


class IsAdminOrShopStaff(permissions.BasePermission):
    """Shop owner + employees."""

    def has_permission(self, request, view):
        return (
            request.user.is_authenticated
            and request.user.role in ("admin", "shop_staff")
        )


class IsCustomerOwner(permissions.BasePermission):
    """Customers can only access objects they own (obj.customer == request.user)."""

    def has_object_permission(self, request, view, obj):
        if request.user.role in ("admin", "shop_staff"):
            return True
        return getattr(obj, "customer", None) == request.user
