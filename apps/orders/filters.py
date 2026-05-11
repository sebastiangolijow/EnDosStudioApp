"""Filterset for `OrderViewSet`.

Drives the admin orders screen's status + date-range filters. The
single FilterSet covers both customer-facing list filtering (which is
narrow today — customers mostly just see their own orders) and the
admin's richer queryset slicing.

Search (icontains across customer email/name/recipient + uuid) is
configured on the ViewSet via `search_fields` — that goes through
DRF's `SearchFilter`, not django-filter, so it lives separately.

Ordering is also a ViewSet concern (DRF `OrderingFilter`).
"""

from django_filters import rest_framework as filters

from .models import Order


class OrderFilter(filters.FilterSet):
    """Admin-friendly filters on top of Order.

    Status & kind are exact matches. Date fields use `gte`/`lte`
    ranges so the frontend can supply ISO datetimes (e.g.
    `?created_after=2026-05-01T00:00:00Z`).

    `status_in` accepts a comma-separated list (`?status_in=paid,in_production`)
    — useful for the admin's "action queue" filter that shows paid +
    in_production together. Implemented separately from `status`
    (single exact) so a callsite can pick the intent.
    """

    status_in = filters.BaseInFilter(field_name="status", lookup_expr="in")
    created_after = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")
    placed_after = filters.IsoDateTimeFilter(field_name="placed_at", lookup_expr="gte")
    placed_before = filters.IsoDateTimeFilter(field_name="placed_at", lookup_expr="lte")

    class Meta:
        model = Order
        fields = ["status", "kind"]
