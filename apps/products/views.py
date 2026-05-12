"""
Product API.

Public list/retrieve so anonymous visitors can browse the catalog without
signing up — drives signups via the buy flow. Staff-only writes via
IsAdminOrShopStaff.

Public retrieve uses slug (better SEO + shareability than UUID); writes
use UUID via /api/v1/admin/products/<uuid>/.
"""
from rest_framework import status, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.core.permissions import IsAdminOrShopStaff

from .models import Category, Product
from .serializers import CategorySerializer, ProductSerializer, ProductWriteSerializer


STAFF_ROLES = {"admin", "shop_staff"}


def _is_staff(user) -> bool:
    return user.is_authenticated and getattr(user, "role", None) in STAFF_ROLES


class ProductViewSet(viewsets.ModelViewSet):
    """Catalog products.

    - list/retrieve: public; non-staff see only is_active=True products.
    - create/update/destroy: IsAdminOrShopStaff.
    - retrieve uses `slug` so URLs are /api/v1/products/llavero-rojo/.
    """

    permission_classes = [AllowAny]  # per-action overrides below
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    lookup_field = "slug"

    def get_queryset(self):
        qs = Product.objects.all()
        if not _is_staff(self.request.user):
            qs = qs.filter(is_active=True)
        # Staff can opt into the public view via ?is_active=true so the
        # /catalogo page shows them exactly what customers see. Without
        # this, the shop owner flipping a product to hidden has no
        # visible effect on /catalogo (their staff role bypasses the
        # filter above), which makes the toggle feel broken.
        is_active_param = self.request.query_params.get("is_active")
        if is_active_param is not None:
            if is_active_param.lower() in {"true", "1", "yes"}:
                qs = qs.filter(is_active=True)
            elif is_active_param.lower() in {"false", "0", "no"}:
                qs = qs.filter(is_active=False)
        return qs

    def get_serializer_class(self):
        if self.action in {"create", "update", "partial_update"}:
            return ProductWriteSerializer
        return ProductSerializer

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "destroy"}:
            return [IsAdminOrShopStaff()]
        return [AllowAny()]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def create(self, request, *args, **kwargs):
        """Return the full read shape (uuid + slug + price_eur) on create.
        ProductWriteSerializer alone would strip those fields and make the
        frontend's data-testid={slug} bindings stale."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            ProductSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        """Same reasoning as create: return the full read shape."""
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(ProductSerializer(serializer.instance).data)

    def destroy(self, request, *args, **kwargs):
        """Override destroy so we can translate PROTECT errors into a friendly 409.

        Order.product uses on_delete=PROTECT; deleting a product that has any
        attached orders raises ProtectedError. Surface as 409 with a hint.
        """
        from django.db.models import ProtectedError
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {
                    "detail": (
                        "Cannot delete a product that has associated orders. "
                        "Set is_active=False to hide it instead."
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """Public read-only category list — drives the admin form's autosuggest.

    Categories are created implicitly when an admin types a new name on
    ProductWriteSerializer. No explicit create/update endpoint; staff edit
    the canonical name via Django admin if they ever need to.
    """

    permission_classes = [AllowAny]
    serializer_class = CategorySerializer
    queryset = Category.objects.all()
