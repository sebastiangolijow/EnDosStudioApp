"""Order API integration tests.

Cover the contract each endpoint promises. Heavy on guards and permissions
because the lifecycle has tight rules.
"""
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.orders.models import Order, OrderFile
from tests.base import BaseTestCase


def _png(name="test.png"):
    return SimpleUploadedFile(name, b"\x89PNG fake", content_type="image/png")


def _fill_draft(order, with_file=True, customer=None):
    """Populate a draft Order with the gold-standard spec ready for place_order.

    Gold standard: vinilo_blanco 10×10 cm × q=100, no add-ons.
    Picked because it sits comfortably above the 20€ floor and exercises
    the full area×quantity×material-rate formula with whole-cm dimensions.
    """
    order.material = "vinilo_blanco"
    order.width_mm = 100
    order.height_mm = 100
    order.quantity = 100
    order.recipient_name = "Test"
    order.street_line_1 = "Carrer 1"
    order.city = "Barcelona"
    order.postal_code = "08001"
    order.country = "ES"
    order.shipping_phone = "+34 600 123 456"  # required at place_order
    order.save()
    if with_file:
        OrderFile.objects.create(
            order=order, kind="original",
            file=_png(),
            created_by=customer or order.created_by,
        )
    return order


class OrderCRUDTests(BaseTestCase):
    def test_customer_can_create_empty_draft(self):
        client, customer = self.authenticate_as_customer()
        response = client.post(reverse("order-list"), data={}, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "draft")
        order = Order.objects.get(pk=response.data["uuid"])
        self.assertEqual(order.created_by, customer)

    def test_customer_lists_only_own_orders(self):
        client_a, customer_a = self.authenticate_as_customer()
        _, customer_b = self.authenticate_as_customer()
        Order.objects.create(created_by=customer_a)
        Order.objects.create(created_by=customer_b)

        response = client_a.get(reverse("order-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["uuid"], str(Order.objects.filter(created_by=customer_a).first().pk))

    def test_staff_lists_all_orders(self):
        _, customer_a = self.authenticate_as_customer()
        _, customer_b = self.authenticate_as_customer()
        Order.objects.create(created_by=customer_a)
        Order.objects.create(created_by=customer_b)
        staff_client, _ = self.authenticate_as_shop_staff()

        response = staff_client.get(reverse("order-list"))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.data["count"], 2)

    def test_customer_cannot_retrieve_another_customers_order(self):
        _, customer_a = self.authenticate_as_customer()
        order = Order.objects.create(created_by=customer_a)
        client_b, _ = self.authenticate_as_customer()
        response = client_b.get(reverse("order-detail", kwargs={"pk": order.pk}))
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_request_rejected(self):
        from rest_framework.test import APIClient
        response = APIClient().get(reverse("order-list"))
        self.assertEqual(response.status_code, 401)

    def test_patch_updates_draft_fields(self):
        client, customer = self.authenticate_as_customer()
        order = Order.objects.create(created_by=customer)
        response = client.patch(
            reverse("order-detail", kwargs={"pk": order.pk}),
            data={"material": "vinilo_blanco", "width_mm": 50, "height_mm": 50, "quantity": 50},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.material, "vinilo_blanco")
        self.assertEqual(order.width_mm, 50)

    def test_patch_blocked_after_placed(self):
        client, customer = self.authenticate_as_customer()
        order = _fill_draft(Order.objects.create(created_by=customer), customer=customer)
        from apps.orders.services import place_order
        place_order(order)
        response = client.patch(
            reverse("order-detail", kwargs={"pk": order.pk}),
            data={"material": "vinilo_blanco"},
            format="json",
        )
        self.assertEqual(response.status_code, 409)


class OrderFileUploadTests(BaseTestCase):
    def test_customer_uploads_original_file(self):
        client, customer = self.authenticate_as_customer()
        order = Order.objects.create(created_by=customer)
        response = client.post(
            reverse("order-files-list", kwargs={"order_pk": order.pk}),
            data={"kind": "original", "file": _png()},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["kind"], "original")
        self.assertGreater(response.data["size_bytes"], 0)

    def test_upload_blocked_after_placed(self):
        client, customer = self.authenticate_as_customer()
        order = _fill_draft(Order.objects.create(created_by=customer), customer=customer)
        from apps.orders.services import place_order
        place_order(order)
        response = client.post(
            reverse("order-files-list", kwargs={"order_pk": order.pk}),
            data={"kind": "die_cut_mask", "file": _png("mask.png")},
            format="multipart",
        )
        # PermissionDenied -> 403 in DRF
        self.assertEqual(response.status_code, 403)

    def test_customer_cannot_upload_to_another_customers_order(self):
        _, customer_a = self.authenticate_as_customer()
        order = Order.objects.create(created_by=customer_a)
        client_b, _ = self.authenticate_as_customer()
        response = client_b.post(
            reverse("order-files-list", kwargs={"order_pk": order.pk}),
            data={"kind": "original", "file": _png()},
            format="multipart",
        )
        self.assertEqual(response.status_code, 403)

    def test_delete_file_then_reupload_works(self):
        client, customer = self.authenticate_as_customer()
        order = Order.objects.create(created_by=customer)
        # First upload
        r1 = client.post(
            reverse("order-files-list", kwargs={"order_pk": order.pk}),
            data={"kind": "original", "file": _png("a.png")},
            format="multipart",
        )
        self.assertEqual(r1.status_code, 201)
        file_pk = r1.data["uuid"]
        # Delete
        r2 = client.delete(reverse("order-files-detail", kwargs={"order_pk": order.pk, "pk": file_pk}))
        self.assertEqual(r2.status_code, 204)
        # Re-upload (would have hit unique_together if delete didn't work)
        r3 = client.post(
            reverse("order-files-list", kwargs={"order_pk": order.pk}),
            data={"kind": "original", "file": _png("b.png")},
            format="multipart",
        )
        self.assertEqual(r3.status_code, 201)


class OrderLifecycleTests(BaseTestCase):
    def test_place_succeeds_with_gold_standard_total(self):
        client, customer = self.authenticate_as_customer()
        order = _fill_draft(Order.objects.create(created_by=customer), customer=customer)
        response = client.post(reverse("order-place", kwargs={"pk": order.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "placed")
        # vinilo_blanco 10×10cm q=100 → ((100+15)/1000)² × 100 × 45€
        # = 0.013225 × 4500 = 59.5125€ → ROUND_HALF_UP → 5951 cents
        self.assertEqual(response.data["total_amount_cents"], 5951)
        self.assertEqual(response.data["total_eur"], "59.51")

    def test_place_fails_409_if_missing_fields(self):
        client, customer = self.authenticate_as_customer()
        order = Order.objects.create(created_by=customer)  # empty draft
        response = client.post(reverse("order-place", kwargs={"pk": order.pk}))
        self.assertEqual(response.status_code, 409)
        self.assertIn("missing", response.data["detail"].lower())

    def test_place_fails_409_if_already_placed(self):
        client, customer = self.authenticate_as_customer()
        order = _fill_draft(Order.objects.create(created_by=customer), customer=customer)
        client.post(reverse("order-place", kwargs={"pk": order.pk}))
        # Second call
        response = client.post(reverse("order-place", kwargs={"pk": order.pk}))
        self.assertEqual(response.status_code, 409)

    def test_cancel_works_while_placed(self):
        client, customer = self.authenticate_as_customer()
        order = _fill_draft(Order.objects.create(created_by=customer), customer=customer)
        client.post(reverse("order-place", kwargs={"pk": order.pk}))
        response = client.post(reverse("order-cancel", kwargs={"pk": order.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "cancelled")

    def test_cancel_blocked_after_paid(self):
        client, customer = self.authenticate_as_customer()
        order = _fill_draft(Order.objects.create(created_by=customer), customer=customer)
        from apps.orders.services import place_order, transition_to_paid
        order = place_order(order)
        transition_to_paid(order, stripe_event={})
        response = client.post(reverse("order-cancel", kwargs={"pk": order.pk}))
        self.assertEqual(response.status_code, 409)

    def test_deliver_blocked_unless_shipped(self):
        client, customer = self.authenticate_as_customer()
        order = _fill_draft(Order.objects.create(created_by=customer), customer=customer)
        from apps.orders.services import place_order
        place_order(order)
        response = client.post(reverse("order-deliver", kwargs={"pk": order.pk}))
        self.assertEqual(response.status_code, 409)

    def test_staff_only_actions_reject_customers(self):
        client, customer = self.authenticate_as_customer()
        order = _fill_draft(Order.objects.create(created_by=customer), customer=customer)
        from apps.orders.services import place_order, transition_to_paid
        order = place_order(order)
        transition_to_paid(order, stripe_event={})
        # Customer hits start-production: forbidden
        response = client.post(reverse("order-start-production", kwargs={"pk": order.pk}))
        self.assertEqual(response.status_code, 403)

    def test_staff_can_run_full_production_flow(self):
        _, customer = self.authenticate_as_customer()
        order = _fill_draft(Order.objects.create(created_by=customer), customer=customer)
        from apps.orders.services import place_order, transition_to_paid
        order = place_order(order)
        transition_to_paid(order, stripe_event={})

        staff_client, _ = self.authenticate_as_shop_staff()
        r1 = staff_client.post(reverse("order-start-production", kwargs={"pk": order.pk}))
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.data["status"], "in_production")

        r2 = staff_client.post(reverse("order-ship", kwargs={"pk": order.pk}))
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data["status"], "shipped")

        # Now the customer can mark delivered
        customer_client = self.authenticate(customer)
        r3 = customer_client.post(reverse("order-deliver", kwargs={"pk": order.pk}))
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(r3.data["status"], "delivered")


class CheckoutTests(BaseTestCase):
    def test_checkout_returns_client_secret(self):
        client, customer = self.authenticate_as_customer()
        order = _fill_draft(Order.objects.create(created_by=customer), customer=customer)
        from apps.orders.services import place_order
        place_order(order)

        fake_intent = {
            "id": "pi_test_checkout_1",
            "client_secret": "pi_test_checkout_1_secret_xyz",
        }
        with mock.patch(
            "apps.orders.views.StripeService.create_payment_intent",
            return_value=fake_intent,
        ) as create_mock:
            response = client.post(reverse("order-checkout", kwargs={"pk": order.pk}))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["client_secret"], "pi_test_checkout_1_secret_xyz")
        self.assertEqual(response.data["payment_intent_id"], "pi_test_checkout_1")
        self.assertEqual(response.data["amount_cents"], 5951)
        self.assertEqual(response.data["currency"], "EUR")

        # Stripe was called with the right amount + metadata
        _, kwargs = create_mock.call_args
        self.assertEqual(kwargs["amount_cents"], 5951)
        self.assertEqual(kwargs["currency"], "eur")
        self.assertEqual(kwargs["order_uuid"], str(order.pk))

        # PI id was denormalized onto the order so the webhook can find it
        order.refresh_from_db()
        self.assertEqual(order.stripe_payment_intent_id, "pi_test_checkout_1")

    def test_checkout_blocked_unless_placed(self):
        client, customer = self.authenticate_as_customer()
        order = Order.objects.create(created_by=customer)  # still draft
        response = client.post(reverse("order-checkout", kwargs={"pk": order.pk}))
        self.assertEqual(response.status_code, 409)

    def test_checkout_502_if_stripe_fails(self):
        client, customer = self.authenticate_as_customer()
        order = _fill_draft(Order.objects.create(created_by=customer), customer=customer)
        from apps.orders.services import place_order
        place_order(order)

        with mock.patch(
            "apps.orders.views.StripeService.create_payment_intent",
            side_effect=RuntimeError("Stripe is down"),
        ):
            response = client.post(reverse("order-checkout", kwargs={"pk": order.pk}))
        self.assertEqual(response.status_code, 502)


class PriceQuoteTests(BaseTestCase):
    def test_quote_gold_standard(self):
        # vinilo_blanco 10×10cm q=100 → 59.51€ — comfortably above the floor
        client, _ = self.authenticate_as_customer()
        response = client.get(
            reverse("order-quote"),
            {
                "material": "vinilo_blanco",
                "width_mm": 100,
                "height_mm": 100,
                "quantity": 100,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_amount_cents"], 5951)
        self.assertEqual(response.data["total_eur"], "59.51")
        self.assertEqual(response.data["currency"], "EUR")

    def test_quote_with_addons(self):
        # vinilo_blanco 10×10cm q=100 +relieve(+35%) +barniz_brillo(+20%)
        # subtotal 5951.25 cents × 1.55 = 9224.4375 → 9224 cents
        client, _ = self.authenticate_as_customer()
        response = client.get(
            reverse("order-quote"),
            {
                "material": "vinilo_blanco",
                "width_mm": 100,
                "height_mm": 100,
                "quantity": 100,
                "with_relief": "true",
                "with_barniz_brillo": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_amount_cents"], 9224)

    def test_quote_floors_small_orders_to_20_eur(self):
        # holografico 5×5cm q=50: subtotal ~10.56€ → floor kicks in
        client, _ = self.authenticate_as_customer()
        response = client.get(
            reverse("order-quote"),
            {
                "material": "holografico",
                "width_mm": 50,
                "height_mm": 50,
                "quantity": 50,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_amount_cents"], 2000)
        self.assertEqual(response.data["total_eur"], "20.00")

    def test_quote_floor_applies_AFTER_addons(self):
        # vinilo_blanco 5×5cm q=20 +relieve: subtotal 3.80€, ×1.35 = 5.13€,
        # still well below 20€ → floor wins, not 5.13×anything.
        client, _ = self.authenticate_as_customer()
        response = client.get(
            reverse("order-quote"),
            {
                "material": "vinilo_blanco",
                "width_mm": 50,
                "height_mm": 50,
                "quantity": 20,
                "with_relief": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_amount_cents"], 2000)

    def test_quote_rejects_non_step_dimensions(self):
        client, _ = self.authenticate_as_customer()
        response = client.get(
            reverse("order-quote"),
            {"material": "vinilo_blanco", "width_mm": 27, "height_mm": 50, "quantity": 20},
        )
        self.assertEqual(response.status_code, 400)

    def test_quote_rejects_below_min_quantity(self):
        client, _ = self.authenticate_as_customer()
        response = client.get(
            reverse("order-quote"),
            {"material": "vinilo_blanco", "width_mm": 50, "height_mm": 50, "quantity": 19},
        )
        self.assertEqual(response.status_code, 400)
