"""Discount routes — mounted at /api/v1/discounts/. Staff-only CRUD."""
from rest_framework.routers import DefaultRouter

from .views import DiscountViewSet


router = DefaultRouter()
router.register(r"", DiscountViewSet, basename="discount")

urlpatterns = router.urls
