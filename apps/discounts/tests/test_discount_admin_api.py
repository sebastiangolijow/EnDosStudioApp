"""Discount admin CRUD — staff-only.

Customers don't list / create / edit discounts. Only the shop owner
manages them via the admin panel (and Django admin).
"""
from django.urls import reverse

from apps.discounts.models import Discount
from tests.base import BaseTestCase


class DiscountPermissionTests(BaseTestCase):
    def test_anon_blocked_from_list(self):
        response = self.client.get(reverse("discount-list"))
        self.assertIn(response.status_code, (401, 403))

    def test_customer_blocked_from_list(self):
        client, _ = self.authenticate_as_customer()
        response = client.get(reverse("discount-list"))
        self.assertEqual(response.status_code, 403)

    def test_customer_blocked_from_create(self):
        client, _ = self.authenticate_as_customer()
        response = client.post(
            reverse("discount-list"),
            data={"code": "summer", "percent_off": 10},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Discount.objects.count(), 0)


class DiscountCRUDTests(BaseTestCase):
    def test_staff_creates_normalizes_code_to_upper(self):
        client, _ = self.authenticate_as_admin()
        response = client.post(
            reverse("discount-list"),
            data={"code": "summer2026", "percent_off": 15, "is_enabled": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["code"], "SUMMER2026")
        d = Discount.objects.get(code="SUMMER2026")
        self.assertEqual(d.percent_off, 15)
        self.assertTrue(d.is_enabled)

    def test_staff_lists_discounts(self):
        Discount.objects.create(code="A", percent_off=10)
        Discount.objects.create(code="B", percent_off=20)
        client, _ = self.authenticate_as_admin()
        response = client.get(reverse("discount-list"))
        self.assertEqual(response.status_code, 200, response.data)
        codes = {d["code"] for d in response.data["results"]}
        self.assertEqual(codes, {"A", "B"})

    def test_staff_disables_discount(self):
        d = Discount.objects.create(code="OLD", percent_off=10, is_enabled=True)
        client, _ = self.authenticate_as_admin()
        response = client.patch(
            reverse("discount-detail", kwargs={"uuid": d.uuid}),
            data={"is_enabled": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        d.refresh_from_db()
        self.assertFalse(d.is_enabled)

    def test_staff_deletes_discount(self):
        d = Discount.objects.create(code="GONE", percent_off=10)
        client, _ = self.authenticate_as_admin()
        response = client.delete(
            reverse("discount-detail", kwargs={"uuid": d.uuid}),
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Discount.objects.filter(uuid=d.uuid).exists())

    def test_percent_off_out_of_range_rejected(self):
        client, _ = self.authenticate_as_admin()
        for bad in [0, 101, -5]:
            response = client.post(
                reverse("discount-list"),
                data={"code": f"BAD{bad}", "percent_off": bad},
                format="json",
            )
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Discount.objects.count(), 0)
