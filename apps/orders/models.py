"""
Order domain models.

Order lifecycle: draft → placed → paid → in_production → shipped → delivered → cancelled.
- draft: customer is editing (uploading images, picking material/size/quantity)
- placed: customer finished editing; awaiting payment
- paid: Stripe webhook confirmed payment_intent.succeeded
- in_production / shipped: admin / shop_staff transitions
- delivered / cancelled: customer (owner) transitions
  (cancel only allowed while {draft, placed} — refunds out of scope for M2)

Pricing depends on material × width_mm × height_mm × quantity, plus two
add-on flags (with_design_service, with_varnish). Mockup rates live in
apps.orders.services. Real numbers come from the shop owner later.
"""
import mimetypes

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from apps.core.models import BaseModel


# Sizing constraints — half-cm increments allowed; minimum 25 mm (2.5 cm)
MIN_DIMENSION_MM = 25
DIMENSION_STEP_MM = 5

# Quantity bounds (enforced both at model and DB level)
MIN_QUANTITY = 20
MAX_QUANTITY = 100_000


STATUS_CHOICES = [
    ("draft", _("Draft")),
    ("placed", _("Placed")),
    ("paid", _("Paid")),
    ("in_production", _("In production")),
    ("shipped", _("Shipped")),
    ("delivered", _("Delivered")),
    ("cancelled", _("Cancelled")),
]

MATERIAL_CHOICES = [
    ("vinilo_blanco", _("Vinilo blanco")),
    ("vinilo_transparente", _("Vinilo transparente")),
    ("holografico", _("Holográfico")),
    ("luminiscente", _("Luminiscente")),
    ("holografico_transparente", _("Holográfico transparente")),
    ("plateado", _("Plateado")),
    ("dorado", _("Dorado")),
    ("eggshell", _("Eggshell")),
    ("eggshell_holografico", _("Eggshell holográfico")),
]

KIND_CHOICES = [
    ("original", _("Original")),
    ("die_cut_mask", _("Die-cut mask")),
    # SVG generated server-side at place_order time. Path the cutter follows
    # at the customer's chosen physical size. Format: SVG (universal —
    # modern cutters + Illustrator + Inkscape all open it).
    ("cut_path", _("Cut path")),
]

# Cut shape — drives the die-cut path generation. `contorneado` follows the
# artwork outline (set by the editor); the other three are geometric primitives
# computed at fulfillment from width_mm × height_mm. Customers picking
# anything other than `contorneado` skip the editor entirely.
SHAPE_CHOICES = [
    ("contorneado", _("Corte contorneado")),
    ("cuadrado", _("Cuadrado")),
    ("circulo", _("Círculo")),
    ("redondeadas", _("Esquinas redondeadas")),
]


class Order(BaseModel):
    """A customer's sticker order. created_by IS the customer (from BaseModel)."""

    status = models.CharField(
        _("status"),
        max_length=20,
        choices=STATUS_CHOICES,
        default="draft",
        db_index=True,
    )

    # Sticker spec (blank/zero while draft; required at place_order time)
    material = models.CharField(
        _("material"),
        max_length=32,
        choices=MATERIAL_CHOICES,
        blank=True,
        default="",
    )
    # Cut shape. Default contorneado matches the existing flow (auto-cut in
    # the editor). When set to one of the geometric primitives (cuadrado,
    # circulo, redondeadas) the editor is skipped and the cut path is
    # computed from width_mm/height_mm at fulfillment time.
    shape = models.CharField(
        _("shape"),
        max_length=20,
        choices=SHAPE_CHOICES,
        default="contorneado",
    )
    # Half-cm increments (multiples of 5 mm); validated at place_order time, not on
    # raw save — drafts can hold partial values while the customer is still editing.
    width_mm = models.PositiveIntegerField(_("width (mm)"), default=0)
    height_mm = models.PositiveIntegerField(_("height (mm)"), default=0)
    # Quantity bounds enforced at place_order time. We keep MinValueValidator(1) so a
    # draft Order can still be saved with quantity=1 (the model default), but
    # place_order() requires the real range (MIN_QUANTITY <= q <= MAX_QUANTITY).
    quantity = models.PositiveIntegerField(
        _("quantity"),
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_QUANTITY)],
    )

    # Add-ons
    with_design_service = models.BooleanField(_("with design service"), default=False)
    with_varnish = models.BooleanField(_("with varnish"), default=False)
    with_relief = models.BooleanField(_("with relief"), default=False)
    relief_note = models.TextField(_("relief note"), blank=True, default="")

    # Shipping (single address per order; structured columns, not a separate model)
    recipient_name = models.CharField(_("recipient name"), max_length=120, blank=True, default="")
    street_line_1 = models.CharField(_("street line 1"), max_length=255, blank=True, default="")
    street_line_2 = models.CharField(_("street line 2"), max_length=255, blank=True, default="")
    city = models.CharField(_("city"), max_length=120, blank=True, default="")
    postal_code = models.CharField(_("postal code"), max_length=20, blank=True, default="")
    country = models.CharField(_("country"), max_length=2, blank=True, default="")

    # Money — store in cents to avoid float math; Stripe wants cents anyway
    total_amount_cents = models.PositiveIntegerField(_("total (cents)"), default=0)
    currency = models.CharField(_("currency"), max_length=3, default="EUR")

    # Stripe linkage — denormalized so the webhook can find the order
    # without joining through PaymentIntent
    stripe_payment_intent_id = models.CharField(
        _("stripe payment intent id"),
        max_length=255,
        blank=True,
        default="",
        db_index=True,
    )

    # Lifecycle timestamps (set by service functions)
    placed_at = models.DateTimeField(_("placed at"), null=True, blank=True)
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True)
    shipped_at = models.DateTimeField(_("shipped at"), null=True, blank=True)
    delivered_at = models.DateTimeField(_("delivered at"), null=True, blank=True)
    cancelled_at = models.DateTimeField(_("cancelled at"), null=True, blank=True)

    history = HistoricalRecords(
        history_user_id_field=models.UUIDField(null=True, blank=True),
    )

    class Meta:
        db_table = "orders_order"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["created_by", "-created_at"]),
        ]

    def __str__(self):
        return f"Order {self.pk} ({self.status})"


def order_file_upload_path(instance, filename):
    """Layout: media/orders/<order_uuid>/<kind>/<filename>"""
    return f"orders/{instance.order.pk}/{instance.kind}/{filename}"


class OrderFile(BaseModel):
    """A file attached to an Order. unique_together(order, kind) — one per slot."""

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="files",
    )
    kind = models.CharField(_("kind"), max_length=20, choices=KIND_CHOICES)
    # FileField (not ImageField) — Pillow validation can reject valid PNGs
    # from OpenCV.js with unusual color modes.
    file = models.FileField(_("file"), upload_to=order_file_upload_path)
    mime_type = models.CharField(_("mime type"), max_length=100, blank=True, default="")
    size_bytes = models.PositiveBigIntegerField(_("size (bytes)"), default=0)

    class Meta:
        db_table = "orders_orderfile"
        ordering = ["-created_at"]
        unique_together = [("order", "kind")]

    def __str__(self):
        return f"OrderFile {self.kind} for order {self.order_id}"

    def save(self, *args, **kwargs):
        if self.file:
            if not self.mime_type:
                guessed_from_upload = getattr(self.file.file, "content_type", None)
                if guessed_from_upload:
                    self.mime_type = guessed_from_upload
                else:
                    self.mime_type = mimetypes.guess_type(self.file.name)[0] or ""
            if not self.size_bytes:
                self.size_bytes = self.file.size
        super().save(*args, **kwargs)
