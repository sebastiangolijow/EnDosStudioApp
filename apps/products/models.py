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


class Category(BaseModel):
    """A reusable category label admins attach to products.

    Free-text from the admin's perspective — they type a name when editing
    a product and previously-used categories show up as suggestions. The
    backend dedupes by slug so "Llaveros" and "llaveros " resolve to the
    same row.
    """

    name = models.CharField(_("name"), max_length=80, unique=True)
    slug = models.SlugField(_("slug"), max_length=100, unique=True, db_index=True)

    class Meta:
        db_table = "products_category"
        ordering = ["name"]
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name) or "categoria"
        super().save(*args, **kwargs)


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
    # Optional discounted price. When set + non-zero, supersedes price_cents
    # for the customer-facing total in the cart and the order subtotal.
    sale_price_cents = models.PositiveIntegerField(
        _("sale price (cents)"), null=True, blank=True
    )
    # 0 means out of stock; backend rejects new orders, frontend disables Comprar.
    stock_quantity = models.PositiveIntegerField(_("stock quantity"), default=0)
    # Shipping uses this when computing courier estimates. Optional today
    # because shipping pricing is currently a fixed multiplier on order
    # subtotal — the field is captured now so weight-aware rates are a
    # data change, not a schema migration, later.
    weight_grams = models.PositiveIntegerField(
        _("weight (grams)"), null=True, blank=True
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        verbose_name=_("category"),
    )
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

    @property
    def effective_price_cents(self) -> int:
        """Price the customer actually pays — sale price when set, else
        the regular price. Used by _compute_catalog_total_cents and by
        the read serializer's effective_price_eur."""
        if self.sale_price_cents:
            return self.sale_price_cents
        return self.price_cents

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
