"""Product admin API integration tests.

Step 2 scope: staff CRUD on /api/v1/products/. Anonymous + customer get
401/403; staff can create (with multipart image upload), update stock,
and destroy. The PROTECT-after-orders test arrives in Step 3 once
Order.product exists.
"""
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from apps.products.models import Product
from tests.base import BaseTestCase


def _png_image(name="prod.png", size=(20, 20)) -> SimpleUploadedFile:
    """Return a minimal valid PNG SimpleUploadedFile.

    Django's ImageField runs Pillow validation; the bytes need to be a real
    image, not a text placeholder.
    """
    buf = BytesIO()
    Image.new("RGB", size, color=(200, 50, 50)).save(buf, format="PNG")
    buf.seek(0)
    return SimpleUploadedFile(name, buf.read(), content_type="image/png")


class ProductWritePermissionTests(BaseTestCase):
    def test_anon_cannot_create(self):
        client = APIClient()
        response = client.post(
            reverse("product-list"),
            data={"name": "X", "price_cents": 100},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_customer_cannot_create(self):
        client, _ = self.authenticate_as_customer()
        response = client.post(
            reverse("product-list"),
            data={"name": "X", "price_cents": 100},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_anon_cannot_update(self):
        product = Product.objects.create(name="X", price_cents=1000, stock_quantity=5)
        client = APIClient()
        response = client.patch(
            reverse("product-detail", kwargs={"slug": product.slug}),
            data={"price_cents": 2000},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_customer_cannot_update(self):
        product = Product.objects.create(name="X", price_cents=1000, stock_quantity=5)
        client, _ = self.authenticate_as_customer()
        response = client.patch(
            reverse("product-detail", kwargs={"slug": product.slug}),
            data={"price_cents": 2000},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_anon_cannot_delete(self):
        product = Product.objects.create(name="X", price_cents=1000, stock_quantity=5)
        client = APIClient()
        response = client.delete(reverse("product-detail", kwargs={"slug": product.slug}))
        self.assertEqual(response.status_code, 401)

    def test_customer_cannot_delete(self):
        product = Product.objects.create(name="X", price_cents=1000, stock_quantity=5)
        client, _ = self.authenticate_as_customer()
        response = client.delete(reverse("product-detail", kwargs={"slug": product.slug}))
        self.assertEqual(response.status_code, 403)


class ProductStaffCRUDTests(BaseTestCase):
    def test_staff_creates_product_with_image(self):
        client, _ = self.authenticate_as_shop_staff()
        response = client.post(
            reverse("product-list"),
            data={
                "name": "Llavero rojo",
                "description": "Acrílico transparente",
                "price_cents": 1500,
                "stock_quantity": 25,
                "image": _png_image("llavero.png"),
                "is_active": "true",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        # ProductWriteSerializer is the response shape on create — minimal fields.
        self.assertEqual(response.data["name"], "Llavero rojo")
        self.assertEqual(response.data["price_cents"], 1500)
        self.assertEqual(response.data["stock_quantity"], 25)
        # Verify it landed in the DB with auto-generated slug + image stored.
        product = Product.objects.get(name="Llavero rojo")
        self.assertEqual(product.slug, "llavero-rojo")
        self.assertTrue(product.image.name)

    def test_admin_creates_product_without_image(self):
        client, _ = self.authenticate_as_admin()
        response = client.post(
            reverse("product-list"),
            data={
                "name": "Sin foto",
                "price_cents": 999,
                "stock_quantity": 0,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        product = Product.objects.get(name="Sin foto")
        self.assertEqual(product.slug, "sin-foto")
        self.assertFalse(product.image)

    def test_staff_patches_stock_quantity(self):
        product = Product.objects.create(name="Llavero", price_cents=1500, stock_quantity=10)
        client, _ = self.authenticate_as_shop_staff()
        response = client.patch(
            reverse("product-detail", kwargs={"slug": product.slug}),
            data={"stock_quantity": 3},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 3)

    def test_staff_can_toggle_is_active(self):
        product = Product.objects.create(name="Llavero", price_cents=1500, stock_quantity=10)
        client, _ = self.authenticate_as_shop_staff()
        response = client.patch(
            reverse("product-detail", kwargs={"slug": product.slug}),
            data={"is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        self.assertFalse(product.is_active)

    def test_staff_destroys_product_without_orders(self):
        product = Product.objects.create(name="Llavero", price_cents=1500, stock_quantity=10)
        client, _ = self.authenticate_as_shop_staff()
        response = client.delete(reverse("product-detail", kwargs={"slug": product.slug}))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Product.objects.filter(pk=product.pk).exists())

    def test_destroy_blocked_with_409_when_product_has_orders(self):
        """PROTECT FK from Order.product → ProtectedError → 409 with hint."""
        from apps.orders.models import KIND_CATALOG, Order

        product = Product.objects.create(name="Llavero", price_cents=1500, stock_quantity=10)
        customer = self.create_customer()
        Order.objects.create(
            kind=KIND_CATALOG,
            product=product,
            product_quantity=1,
            created_by=customer,
        )

        client, _ = self.authenticate_as_shop_staff()
        response = client.delete(reverse("product-detail", kwargs={"slug": product.slug}))

        self.assertEqual(response.status_code, 409)
        self.assertIn("is_active", response.data["detail"])
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())
