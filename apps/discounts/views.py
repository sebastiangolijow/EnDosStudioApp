"""Discount admin CRUD.

Staff-only — customers don't browse / list discounts; they just submit
codes via apps.orders.views.OrderViewSet.apply_discount. This ViewSet
is exclusively for the shop owner's admin panel.
"""
from rest_framework import viewsets

from apps.core.permissions import IsAdminOrShopStaff

from .models import Discount
from .serializers import DiscountSerializer


class DiscountViewSet(viewsets.ModelViewSet):
    """Full CRUD on Discount, gated to staff.

    Lookup by uuid (consistent with the rest of the API). No search /
    filter params today — the admin table is small enough that a
    client-side filter is fine.
    """

    permission_classes = [IsAdminOrShopStaff]
    serializer_class = DiscountSerializer
    queryset = Discount.objects.all().order_by("-created_at")
    lookup_field = "uuid"
