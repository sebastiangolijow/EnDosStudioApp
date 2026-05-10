"""
Order API views.

Views stay thin: they validate input, dispatch to a service function,
translate service exceptions to HTTP. Business logic lives in services.py.

Endpoint surface:
  GET    /api/v1/orders/                  list (customer: own; staff: all)
  POST   /api/v1/orders/                  create empty draft for the customer
  GET    /api/v1/orders/{uuid}/           retrieve
  PATCH  /api/v1/orders/{uuid}/           edit (draft only)
  DELETE /api/v1/orders/{uuid}/           NOT supported (use cancel)
  POST   /api/v1/orders/{uuid}/place/     draft → placed (customer)
  POST   /api/v1/orders/{uuid}/checkout/  → Stripe PaymentIntent (customer)
  POST   /api/v1/orders/{uuid}/cancel/    → cancelled (customer, only draft/placed)
  POST   /api/v1/orders/{uuid}/deliver/   → delivered (customer)
  POST   /api/v1/orders/{uuid}/start-production/  paid → in_production (staff)
  POST   /api/v1/orders/{uuid}/ship/      in_production → shipped (staff)

  POST   /api/v1/orders/{uuid}/files/     upload OrderFile (multipart)
  DELETE /api/v1/orders/{uuid}/files/{file_uuid}/  remove OrderFile

  POST   /api/v1/orders/{uuid}/smart-cut/ AI background-removal cut polygon

  GET    /api/v1/orders/quote/            price preview, no order needed
"""
import logging

from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.payments.services import StripeService

from .models import Order, OrderFile
from .serializers import (
    CheckoutResponseSerializer,
    OrderCreateSerializer,
    OrderFileSerializer,
    OrderSerializer,
    OrderUpdateSerializer,
    PriceQuoteSerializer,
)
from .services import (
    InvalidPricingInput,
    InvalidTransition,
    cancel_order,
    compute_total_cents,
    mark_delivered,
    place_order,
    transition_to_in_production,
    transition_to_shipped,
)
from .services_smart_cut import (
    NoOriginalFile,
    SmartCutModelUnavailable,
    smart_cut_for_order,
)

logger = logging.getLogger(__name__)

STAFF_ROLES = {"admin", "shop_staff"}


def _is_staff(user) -> bool:
    return user.is_authenticated and user.role in STAFF_ROLES


class OrderViewSet(viewsets.ModelViewSet):
    """Order CRUD + lifecycle actions.

    Customers see/edit only their own orders. Staff see all orders. PATCH
    is the only allowed update verb (PUT not enabled). DELETE is disabled
    in favor of the cancel action.
    """

    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "head", "options"]
    lookup_field = "pk"

    def get_queryset(self):
        qs = Order.objects.all().prefetch_related("files")
        if _is_staff(self.request.user):
            return qs
        return qs.filter(created_by=self.request.user)

    def get_serializer_class(self):
        if self.action == "create":
            return OrderCreateSerializer
        if self.action in {"update", "partial_update"}:
            return OrderUpdateSerializer
        return OrderSerializer

    def perform_create(self, serializer):
        # Customer creates either an empty sticker draft or a catalog
        # draft with product+qty already set; staff creating orders for
        # someone else is out of scope for M3a.
        serializer.save(created_by=self.request.user)

    def create(self, request, *args, **kwargs):
        """Override to return the full OrderSerializer shape after create.

        OrderCreateSerializer takes minimal input (kind/product/qty); the
        frontend wants the full Order back so it can route to checkout.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        order = serializer.instance
        return Response(
            OrderSerializer(order).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        order = self.get_object()
        if order.status != "draft":
            return Response(
                {"detail": f"Cannot edit order in status {order.status!r}; only drafts are editable."},
                status=status.HTTP_409_CONFLICT,
            )
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        order = self.get_object()
        if order.status != "draft":
            return Response(
                {"detail": f"Cannot edit order in status {order.status!r}; only drafts are editable."},
                status=status.HTTP_409_CONFLICT,
            )
        # Use OrderUpdateSerializer for INPUT validation but return the
        # full read-shape OrderSerializer for OUTPUT — otherwise the
        # response misses fields like `uuid`, `status`, `total_amount_cents`,
        # etc., which the frontend needs.
        #
        # The default ModelViewSet.partial_update uses get_serializer_class
        # for both directions, so a stripped write-only serializer leaks
        # into the response. Mirrors the same workaround ProductViewSet
        # uses (per CLAUDE.md "Backend response shape note").
        instance = self.get_object()
        write_serializer = self.get_serializer(instance, data=request.data, partial=True)
        write_serializer.is_valid(raise_exception=True)
        self.perform_update(write_serializer)
        return Response(OrderSerializer(instance).data)

    # ----- lifecycle actions -----

    @action(detail=True, methods=["post"])
    def place(self, request, pk=None):
        order = self.get_object()
        try:
            order = place_order(order)
        except InvalidTransition as e:
            return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)
        except InvalidPricingInput as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OrderSerializer(order).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def checkout(self, request, pk=None):
        """Create a Stripe PaymentIntent for a placed order; return client_secret."""
        order = self.get_object()
        if order.status != "placed":
            return Response(
                {"detail": f"Order must be 'placed' to checkout; current status: {order.status!r}."},
                status=status.HTTP_409_CONFLICT,
            )
        if order.total_amount_cents <= 0:
            return Response(
                {"detail": "Order total is zero; cannot create payment intent."},
                status=status.HTTP_409_CONFLICT,
            )

        # Catalog re-check: customer placed the order; stock may have moved
        # since. Reject before charging Stripe (cleaner than processing a
        # refund). The race-safe decrement still happens in transition_to_paid.
        from .models import KIND_CATALOG
        if order.kind == KIND_CATALOG and order.product_id is not None:
            order.product.refresh_from_db()
            if order.product.stock_quantity < order.product_quantity:
                return Response(
                    {
                        "detail": "insufficient_stock",
                        "message": (
                            f"Only {order.product.stock_quantity} unit(s) of "
                            f"'{order.product.name}' remain in stock."
                        ),
                    },
                    status=status.HTTP_409_CONFLICT,
                )

        try:
            intent = StripeService().create_payment_intent(
                amount_cents=order.total_amount_cents,
                currency=(order.currency or "EUR").lower(),
                order_uuid=str(order.pk),
            )
        except Exception as e:
            logger.exception("Stripe create_payment_intent failed for order %s", order.pk)
            return Response(
                {"detail": f"Payment provider error: {e}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Denormalize the PI id onto the order so the webhook can find it
        # even if metadata gets stripped along the way.
        order.stripe_payment_intent_id = intent["id"]
        order.save(update_fields=["stripe_payment_intent_id", "updated_at"])

        payload = {
            "client_secret": intent["client_secret"],
            "payment_intent_id": intent["id"],
            "amount_cents": order.total_amount_cents,
            "currency": order.currency,
        }
        return Response(
            CheckoutResponseSerializer(payload).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        try:
            order = cancel_order(order, actor=request.user, reason=request.data.get("reason", ""))
        except InvalidTransition as e:
            return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def deliver(self, request, pk=None):
        order = self.get_object()
        try:
            order = mark_delivered(order, actor=request.user)
        except InvalidTransition as e:
            return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"], url_path="start-production")
    def start_production(self, request, pk=None):
        if not _is_staff(request.user):
            raise PermissionDenied("Staff only.")
        order = self.get_object()
        try:
            order = transition_to_in_production(order, actor=request.user)
        except InvalidTransition as e:
            return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def ship(self, request, pk=None):
        if not _is_staff(request.user):
            raise PermissionDenied("Staff only.")
        order = self.get_object()
        try:
            order = transition_to_shipped(order, actor=request.user)
        except InvalidTransition as e:
            return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"], url_path="smart-cut")
    def smart_cut(self, request, pk=None):
        """Run AI background removal on the order's `original` image.

        Returns a polygon the editor can pass to `setMask`. Sync, blocking
        (~2-4 s on CPU); see services_smart_cut.smart_cut_for_order. Allowed
        on any status — read-only, doesn't mutate the order. Ownership is
        enforced via `get_queryset` (customers see only their own orders).

        Optional `margin_mm` (body or query param) controls the bleed margin
        added around the detected silhouette. Defaults to 15 mm; floored at
        the printable minimum (5 mm) inside the service.

        Optional `smoothness` (1-10) controls how aggressively the cut line
        rounds sharp concavities. Defaults to 5 (cuttable on most plotters).
        """
        order = self.get_object()
        margin_raw = request.data.get("margin_mm") if request.data else None
        if margin_raw is None:
            margin_raw = request.query_params.get("margin_mm")
        try:
            margin_mm = int(margin_raw) if margin_raw is not None else 15
        except (TypeError, ValueError):
            return Response(
                {"detail": "margin_mm must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        smooth_raw = request.data.get("smoothness") if request.data else None
        if smooth_raw is None:
            smooth_raw = request.query_params.get("smoothness")
        try:
            smoothness = int(smooth_raw) if smooth_raw is not None else 5
        except (TypeError, ValueError):
            return Response(
                {"detail": "smoothness must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = smart_cut_for_order(
                order, margin_mm=margin_mm, smoothness=smoothness
            )
        except NoOriginalFile:
            return Response(
                {"detail": "No original file uploaded."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except SmartCutModelUnavailable as exc:
            logger.exception(
                "Smart-cut model unavailable for order %s", order.pk,
            )
            return Response(
                {"detail": f"Smart cut unavailable: {exc}"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(result, status=status.HTTP_200_OK)


class OrderFileViewSet(viewsets.ModelViewSet):
    """Files attached to an order. Mounted at /orders/{order_pk}/files/.

    Customer must own the order; only allowed while status='draft' (you can't
    swap files on a placed order). Re-uploading the same `kind` replaces via
    DELETE then POST (the unique_together constraint means we need to delete
    first, not 'overwrite' on POST).
    """

    permission_classes = [IsAuthenticated]
    serializer_class = OrderFileSerializer
    http_method_names = ["get", "post", "delete", "head", "options"]
    lookup_field = "pk"

    def get_queryset(self):
        qs = OrderFile.objects.filter(order_id=self.kwargs["order_pk"])
        if _is_staff(self.request.user):
            return qs
        return qs.filter(order__created_by=self.request.user)

    def _get_order(self) -> Order:
        order = get_object_or_404(Order, pk=self.kwargs["order_pk"])
        if not _is_staff(self.request.user) and order.created_by_id != self.request.user.pk:
            raise PermissionDenied("Not your order.")
        return order

    def perform_create(self, serializer):
        order = self._get_order()
        if order.status != "draft":
            raise PermissionDenied(
                f"Cannot modify files on order in status {order.status!r}; only drafts."
            )
        serializer.save(order=order, created_by=self.request.user)

    def perform_destroy(self, instance):
        if instance.order.status != "draft":
            raise PermissionDenied(
                f"Cannot remove files from order in status {instance.order.status!r}; only drafts."
            )
        instance.delete()


class PriceQuoteView(APIView):
    """GET /api/v1/orders/quote/?material=...&width_mm=...&height_mm=...&quantity=...

    Pure pricing preview. Doesn't touch the database.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        ser = PriceQuoteSerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)
        try:
            total_cents = compute_total_cents(**ser.validated_data)
        except InvalidPricingInput as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {
                "total_amount_cents": total_cents,
                "total_eur": f"{total_cents / 100:.2f}",
                "currency": "EUR",
            },
            status=status.HTTP_200_OK,
        )
