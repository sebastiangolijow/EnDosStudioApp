"""
Catalog product model — non-sticker items (llaveros, etc.) the shop sells
alongside custom stickers. Per the M3a plan, a product is purchased via a
catalog Order (Order.kind="catalog") that reuses the existing Stripe +
shipping + admin pipeline. Mixed cart with stickers is deferred to M3b.
"""
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import BaseModel


def product_image_upload_path(instance, filename):
    """Layout: media/products/<product_uuid>/<filename>"""
    return f"products/{instance.pk}/{filename}"


class Product(BaseModel):
    """A non-sticker item in the shop's catalog.

    Pricing is a flat per-unit `price_cents` (no formula, unlike stickers).
    Stock is decremented on payment inside `transition_to_paid` — see
    apps.orders.services. Owner can hide a product without deleting it via
    `is_active=False`; this also keeps the product retrievable for past
    orders that reference it (PROTECT FK from Order.product).
    """

    name = models.CharField(_("name"), max_length=120)
    # Auto-populated from name in save(); used for public URLs at /catalogo/<slug>.
    slug = models.SlugField(_("slug"), max_length=140, unique=True, db_index=True)
    description = models.TextField(_("description"), blank=True, default="")
    # Integer cents (matches Order.total_amount_cents convention)
    price_cents = models.PositiveIntegerField(_("price (cents)"))
    # 0 means out of stock; backend rejects new orders, frontend disables Comprar.
    stock_quantity = models.PositiveIntegerField(_("stock quantity"), default=0)
    image = models.ImageField(
        _("image"),
        upload_to=product_image_upload_path,
        blank=True,
        null=True,
    )
    is_active = models.BooleanField(_("is active"), default=True, db_index=True)

    history = HistoricalRecords(
        history_user_id_field=models.UUIDField(null=True, blank=True),
    )

    class Meta:
        db_table = "products_product"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_active", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.slug})"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._build_unique_slug()
        super().save(*args, **kwargs)

    def _build_unique_slug(self) -> str:
        """slugify(name), suffix `-2`, `-3`, ... on collision."""
        base = slugify(self.name) or "producto"
        candidate = base
        n = 2
        while Product.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
            candidate = f"{base}-{n}"
            n += 1
        return candidate
