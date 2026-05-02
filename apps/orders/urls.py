"""Order routes."""
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import OrderFileViewSet, OrderViewSet, PriceQuoteView

router = DefaultRouter()
router.register(r"", OrderViewSet, basename="order")

# OrderFile views — manual paths since DRF's DefaultRouter doesn't nest natively
# and we don't want a drf-nested-routers dep just for two endpoints.
order_file_list = OrderFileViewSet.as_view({"get": "list", "post": "create"})
order_file_detail = OrderFileViewSet.as_view({"get": "retrieve", "delete": "destroy"})

urlpatterns = [
    # /api/v1/orders/quote/ must come BEFORE the router so the router doesn't
    # try to match "quote" as a UUID lookup.
    path("quote/", PriceQuoteView.as_view(), name="order-quote"),
    path(
        "<uuid:order_pk>/files/",
        order_file_list,
        name="order-files-list",
    ),
    path(
        "<uuid:order_pk>/files/<uuid:pk>/",
        order_file_detail,
        name="order-files-detail",
    ),
] + router.urls
