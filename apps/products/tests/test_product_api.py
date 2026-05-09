"""Product API integration tests.

Step 1 scope: public read-only endpoints (list + retrieve) and the slug
lookup. Staff CRUD tests live alongside in test_product_admin_api.py
once Step 2 lands.
"""
from django.urls import reverse
from rest_framework.test import APIClient

from apps.products.models import Product
from tests.base import BaseTestCase


class ProductPublicListTests(BaseTestCase):
    def test_anon_can_list_active_products(self):
        Product.objects.create(name="Llavero rojo", price_cents=1500, stock_quantity=10)
        Product.objects.create(name="Llavero azul", price_cents=1500, stock_quantity=5)
        client = APIClient()  # no auth

        response = client.get(reverse("product-list"))

        self.assertEqual(response.status_code, 200)
        names = sorted(p["name"] for p in response.data["results"])
        self.assertEqual(names, ["Llavero azul", "Llavero rojo"])

    def test_inactive_products_hidden_from_public_list(self):
        Product.objects.create(name="Visible", price_cents=1500, stock_quantity=10)
        Product.objects.create(
            name="Oculto",
            price_cents=1500,
            stock_quantity=10,
            is_active=False,
        )
        client = APIClient()

        response = client.get(reverse("product-list"))

        self.assertEqual(response.status_code, 200)
        names = [p["name"] for p in response.data["results"]]
        self.assertEqual(names, ["Visible"])

    def test_staff_sees_inactive_products(self):
        Product.objects.create(name="Visible", price_cents=1500, stock_quantity=10)
        Product.objects.create(
            name="Oculto",
            price_cents=1500,
            stock_quantity=10,
            is_active=False,
        )
        client, _ = self.authenticate_as_shop_staff()

        response = client.get(reverse("product-list"))

        self.assertEqual(response.status_code, 200)
        names = sorted(p["name"] for p in response.data["results"])
        self.assertEqual(names, ["Oculto", "Visible"])


class ProductPublicRetrieveTests(BaseTestCase):
    def test_anon_can_retrieve_active_by_slug(self):
        product = Product.objects.create(
            name="Llavero rojo",
            price_cents=1500,
            stock_quantity=10,
            description="Acrílico transparente",
        )
        client = APIClient()

        response = client.get(reverse("product-detail", kwargs={"slug": product.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["name"], "Llavero rojo")
        self.assertEqual(response.data["slug"], "llavero-rojo")
        self.assertEqual(response.data["price_cents"], 1500)
        self.assertEqual(response.data["price_eur"], "15.00")
        self.assertEqual(response.data["stock_quantity"], 10)
        self.assertEqual(response.data["description"], "Acrílico transparente")
        self.assertTrue(response.data["is_active"])

    def test_inactive_product_404_for_anon(self):
        product = Product.objects.create(
            name="Oculto",
            price_cents=1500,
            stock_quantity=10,
            is_active=False,
        )
        client = APIClient()

        response = client.get(reverse("product-detail", kwargs={"slug": product.slug}))

        self.assertEqual(response.status_code, 404)


class ProductSlugTests(BaseTestCase):
    def test_slug_auto_generated_from_name(self):
        product = Product.objects.create(name="Llavero Rojo Brillante", price_cents=1500)
        self.assertEqual(product.slug, "llavero-rojo-brillante")

    def test_slug_collision_gets_numeric_suffix(self):
        Product.objects.create(name="Llavero rojo", price_cents=1500)
        second = Product.objects.create(name="Llavero rojo", price_cents=1500)
        third = Product.objects.create(name="Llavero rojo", price_cents=1500)

        self.assertEqual(second.slug, "llavero-rojo-2")
        self.assertEqual(third.slug, "llavero-rojo-3")

    def test_explicit_slug_respected(self):
        product = Product.objects.create(
            name="Algo distinto",
            price_cents=1500,
            slug="custom-slug",
        )
        self.assertEqual(product.slug, "custom-slug")
