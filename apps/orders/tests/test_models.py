"""Order model-level tests.

Currently focused on the kind XOR enforced in Order.clean(). Field-level
required-ness is handled by place_order (services.py); clean() only
guards the kind-vs-product invariant so a draft can never be saved with
both sticker and catalog data set at once.
"""
from django.core.exceptions import ValidationError

from apps.orders.models import KIND_CATALOG, KIND_STICKER, Order
from apps.products.models import Product
from tests.base import BaseTestCase


class OrderKindXORTests(BaseTestCase):
    def test_sticker_order_with_product_set_raises(self):
        product = Product.objects.create(name="Llavero", price_cents=1500, stock_quantity=10)
        customer = self.create_customer()
        order = Order(
            kind=KIND_STICKER,
            product=product,  # not allowed for sticker kind
            created_by=customer,
        )
        with self.assertRaises(ValidationError) as ctx:
            order.full_clean()
        self.assertIn("product", ctx.exception.message_dict)

    def test_sticker_order_with_product_quantity_set_raises(self):
        customer = self.create_customer()
        order = Order(
            kind=KIND_STICKER,
            product_quantity=2,
            created_by=customer,
        )
        with self.assertRaises(ValidationError) as ctx:
            order.full_clean()
        self.assertIn("product_quantity", ctx.exception.message_dict)

    def test_catalog_order_without_product_raises(self):
        customer = self.create_customer()
        order = Order(
            kind=KIND_CATALOG,
            product=None,
            product_quantity=1,
            created_by=customer,
        )
        with self.assertRaises(ValidationError) as ctx:
            order.full_clean()
        self.assertIn("product", ctx.exception.message_dict)

    def test_catalog_order_with_zero_quantity_raises(self):
        product = Product.objects.create(name="Llavero", price_cents=1500, stock_quantity=10)
        customer = self.create_customer()
        order = Order(
            kind=KIND_CATALOG,
            product=product,
            product_quantity=0,
            created_by=customer,
        )
        with self.assertRaises(ValidationError) as ctx:
            order.full_clean()
        self.assertIn("product_quantity", ctx.exception.message_dict)

    def test_catalog_order_valid_with_product_and_quantity(self):
        product = Product.objects.create(name="Llavero", price_cents=1500, stock_quantity=10)
        customer = self.create_customer()
        order = Order(
            kind=KIND_CATALOG,
            product=product,
            product_quantity=1,
            created_by=customer,
        )
        order.full_clean()  # should NOT raise

    def test_default_sticker_order_passes_clean(self):
        """A bare draft sticker order (no product, qty=0) is valid."""
        customer = self.create_customer()
        order = Order(created_by=customer)
        order.full_clean()  # should NOT raise — kind defaults to "sticker"
