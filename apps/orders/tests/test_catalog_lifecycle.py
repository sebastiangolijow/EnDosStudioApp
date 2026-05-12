"""Catalog order lifecycle tests.

Covers the M3a place_order + transition_to_paid catalog branches: pricing
from Product.price_cents, stock decrement on payment, oversell logging,
and that the cut-path generator is NOT called for catalog orders.

Sticker regression tests live in test_views.py and test_cut_path.py and
must remain green; this file only adds catalog-specific coverage.
"""
from unittest import mock

from django.urls import reverse

from apps.orders.models import KIND_CATALOG, KIND_STICKER, Order
from apps.orders.services import (
    InvalidTransition,
    place_order,
    transition_to_paid,
)
from apps.products.models import Product
from tests.base import BaseTestCase


def _fill_catalog_draft(order, product, qty=2):
    order.kind = KIND_CATALOG
    order.product = product
    order.product_quantity = qty
    order.recipient_name = "Test"
    order.street_line_1 = "Carrer 1"
    order.city = "Barcelona"
    order.postal_code = "08001"
    order.country = "ES"
    order.shipping_phone = "+34 600 123 456"
    order.save()
    return order


class CatalogPlaceOrderTests(BaseTestCase):
    def test_place_order_happy_path_prices_product_x_qty(self):
        product = Product.objects.create(name="Llavero", price_cents=1500, stock_quantity=10)
        customer = self.create_customer()
        order = _fill_catalog_draft(
            Order.objects.create(kind=KIND_CATALOG, created_by=customer),
            product,
            qty=3,
        )

        order = place_order(order)

        self.assertEqual(order.status, "placed")
        # pre-IVA: 1500 cents × 3 = 4500 cents
        # all-in:  4500 × 1.21 = 5445 cents
        self.assertEqual(order.total_amount_cents, 5445)

    def test_place_order_missing_product_blocks(self):
        customer = self.create_customer()
        order = Order.objects.create(kind=KIND_CATALOG, created_by=customer)
        # Skip _fill_catalog_draft — no product attached
        order.recipient_name = "Test"
        order.street_line_1 = "Carrer 1"
        order.city = "Barcelona"
        order.postal_code = "08001"
        order.country = "ES"
        order.shipping_phone = "+34 600 123 456"
        order.save()

        with self.assertRaises(InvalidTransition) as ctx:
            place_order(order)
        self.assertIn("product", str(ctx.exception))

    def test_place_order_insufficient_stock_blocks(self):
        product = Product.objects.create(name="Escaso", price_cents=1500, stock_quantity=2)
        customer = self.create_customer()
        order = _fill_catalog_draft(
            Order.objects.create(kind=KIND_CATALOG, created_by=customer),
            product,
            qty=5,  # > stock_quantity
        )

        with self.assertRaises(InvalidTransition) as ctx:
            place_order(order)
        self.assertIn("insufficient_stock", str(ctx.exception))

    def test_place_order_inactive_product_blocks(self):
        product = Product.objects.create(
            name="Oculto",
            price_cents=1500,
            stock_quantity=10,
            is_active=False,
        )
        customer = self.create_customer()
        order = _fill_catalog_draft(
            Order.objects.create(kind=KIND_CATALOG, created_by=customer),
            product,
            qty=1,
        )

        with self.assertRaises(InvalidTransition) as ctx:
            place_order(order)
        self.assertIn("inactive", str(ctx.exception))


class CatalogTransitionToPaidTests(BaseTestCase):
    def test_transition_to_paid_decrements_stock(self):
        product = Product.objects.create(name="Llavero", price_cents=1500, stock_quantity=10)
        customer = self.create_customer()
        order = _fill_catalog_draft(
            Order.objects.create(kind=KIND_CATALOG, created_by=customer),
            product,
            qty=3,
        )
        order = place_order(order)

        with mock.patch("apps.orders.cut_path.generate_cut_path_file") as cut_mock:
            order = transition_to_paid(order, stripe_event={})

        self.assertEqual(order.status, "paid")
        # Cut-path generation must NOT run for catalog orders
        cut_mock.assert_not_called()

        product.refresh_from_db()
        self.assertEqual(product.stock_quantity, 7)  # 10 − 3

    def test_oversell_logged_but_payment_succeeds(self):
        """If stock dropped between place and paid (race), oversell is allowed
        with a warning so the shop reconciles manually."""
        product = Product.objects.create(name="Llavero", price_cents=1500, stock_quantity=5)
        customer = self.create_customer()
        order = _fill_catalog_draft(
            Order.objects.create(kind=KIND_CATALOG, created_by=customer),
            product,
            qty=3,
        )
        order = place_order(order)

        # Simulate concurrent buyer draining the stock between placement and payment
        Product.objects.filter(pk=product.pk).update(stock_quantity=1)

        with self.assertLogs("apps.orders.services", level="WARNING") as cm:
            order = transition_to_paid(order, stripe_event={})

        self.assertEqual(order.status, "paid")
        self.assertTrue(any("Oversell" in line for line in cm.output))

        product.refresh_from_db()
        # max(0, 1 − 3) = 0, NOT a negative number
        self.assertEqual(product.stock_quantity, 0)


class CatalogCheckoutStockGuardTests(BaseTestCase):
    """At checkout time we re-check stock — defends against the customer
    placing the order, walking away, and someone else draining stock in
    between. Rejecting here is cleaner than charging Stripe + refunding."""

    def _seed_placed_catalog_order(self, product_stock=10, qty=2):
        product = Product.objects.create(
            name="Llavero",
            price_cents=1500,
            stock_quantity=product_stock,
        )
        customer = self.create_customer()
        order = _fill_catalog_draft(
            Order.objects.create(kind=KIND_CATALOG, created_by=customer),
            product,
            qty=qty,
        )
        order = place_order(order)
        return product, order, customer

    def test_checkout_409_when_stock_dropped_below_qty(self):
        product, order, customer = self._seed_placed_catalog_order(product_stock=10, qty=3)

        # Concurrent buyer drained stock to 1 between place and checkout
        Product.objects.filter(pk=product.pk).update(stock_quantity=1)

        client = self.authenticate(customer)
        response = client.post(reverse("order-checkout", kwargs={"pk": order.pk}))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["detail"], "insufficient_stock")
        self.assertIn("Llavero", response.data["message"])

    def test_checkout_succeeds_when_stock_still_sufficient(self):
        product, order, customer = self._seed_placed_catalog_order(product_stock=10, qty=3)

        client = self.authenticate(customer)
        fake_intent = {"id": "pi_test_catalog_1", "client_secret": "pi_test_catalog_1_secret"}
        with mock.patch(
            "apps.orders.views.StripeService.create_payment_intent",
            return_value=fake_intent,
        ):
            response = client.post(reverse("order-checkout", kwargs={"pk": order.pk}))

        self.assertEqual(response.status_code, 200, response.data)
        # 1500 × 3 = 4500 pre-IVA; ×1.21 = 5445 all-in.
        self.assertEqual(response.data["amount_cents"], 5445)


class StickerRegressionTests(BaseTestCase):
    """Spot-checks that sticker orders still work after the service refactor.

    The full sticker test suite in test_views.py is the real regression net;
    these tests are belt-and-braces for the kind branching code path.
    """

    def test_sticker_place_unchanged(self):
        from apps.orders.tests.test_views import _fill_draft, _png
        from apps.orders.models import OrderFile

        customer = self.create_customer()
        order = _fill_draft(Order.objects.create(created_by=customer), customer=customer)

        order = place_order(order)

        self.assertEqual(order.status, "placed")
        self.assertEqual(order.kind, KIND_STICKER)
        # Default sticker order from _fill_draft is the gold standard
        # (vinilo_blanco 10×10cm q=100 → 5951 cents pre-IVA, ×1.21 → 7201).
        self.assertEqual(order.total_amount_cents, 7201)

    def test_sticker_transition_to_paid_still_calls_cut_path(self):
        from apps.orders.tests.test_views import _fill_draft

        customer = self.create_customer()
        order = _fill_draft(Order.objects.create(created_by=customer), customer=customer)
        order = place_order(order)

        with mock.patch("apps.orders.cut_path.generate_cut_path_file") as cut_mock:
            order = transition_to_paid(order, stripe_event={})

        self.assertEqual(order.status, "paid")
        cut_mock.assert_called_once()
