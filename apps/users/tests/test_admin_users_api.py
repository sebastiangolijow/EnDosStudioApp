"""Admin users page — staff list customers + toggle can_reserve_orders.

GET  /api/v1/users/                staff-only list with search + filter
PATCH /api/v1/users/{uuid}/        staff-only flip of can_reserve_orders
"""
from django.urls import reverse

from tests.base import BaseTestCase


class AdminUserListPermissionTests(BaseTestCase):
    def test_anon_blocked(self):
        response = self.client.get(reverse("admin-user-list"))
        self.assertIn(response.status_code, (401, 403))

    def test_customer_blocked(self):
        client, _ = self.authenticate_as_customer()
        response = client.get(reverse("admin-user-list"))
        self.assertEqual(response.status_code, 403)


class AdminUserListTests(BaseTestCase):
    def test_staff_lists_all_users(self):
        client, _ = self.authenticate_as_admin()
        # The auth helper already created an admin row; create 2 more
        # so the list is non-trivial.
        self.create_customer(email="a@example.com")
        self.create_customer(email="b@example.com")
        response = client.get(reverse("admin-user-list"))
        self.assertEqual(response.status_code, 200, response.data)
        # Paginated response: results is the list.
        emails = {u["email"] for u in response.data["results"]}
        self.assertIn("a@example.com", emails)
        self.assertIn("b@example.com", emails)

    def test_search_filters_by_email(self):
        client, _ = self.authenticate_as_admin()
        self.create_customer(email="alpha@example.com")
        self.create_customer(email="beta@example.com")
        response = client.get(reverse("admin-user-list"), {"search": "alpha"})
        self.assertEqual(response.status_code, 200)
        emails = {u["email"] for u in response.data["results"]}
        self.assertIn("alpha@example.com", emails)
        self.assertNotIn("beta@example.com", emails)

    def test_can_reserve_orders_filter(self):
        client, _ = self.authenticate_as_admin()
        self.create_customer(email="trusted@example.com", can_reserve_orders=True)
        self.create_customer(email="regular@example.com", can_reserve_orders=False)
        response = client.get(
            reverse("admin-user-list"), {"can_reserve_orders": "true"}
        )
        self.assertEqual(response.status_code, 200)
        emails = {u["email"] for u in response.data["results"]}
        self.assertIn("trusted@example.com", emails)
        self.assertNotIn("regular@example.com", emails)


class AdminUserPatchTests(BaseTestCase):
    def test_staff_flips_can_reserve_orders(self):
        client, _ = self.authenticate_as_admin()
        customer = self.create_customer(can_reserve_orders=False)
        response = client.patch(
            reverse("admin-user-detail", kwargs={"uuid": customer.uuid}),
            data={"can_reserve_orders": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["can_reserve_orders"])
        customer.refresh_from_db()
        self.assertTrue(customer.can_reserve_orders)

    def test_customer_cannot_patch_another_user(self):
        target = self.create_customer()
        client, _ = self.authenticate_as_customer()
        response = client.patch(
            reverse("admin-user-detail", kwargs={"uuid": target.uuid}),
            data={"can_reserve_orders": True},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        target.refresh_from_db()
        self.assertFalse(target.can_reserve_orders)

    def test_admin_write_serializer_ignores_other_fields(self):
        """Trying to PATCH role or is_active via the admin user endpoint
        does nothing — the AdminUserWriteSerializer only declares
        can_reserve_orders. Other fields silently drop."""
        client, _ = self.authenticate_as_admin()
        customer = self.create_customer()
        response = client.patch(
            reverse("admin-user-detail", kwargs={"uuid": customer.uuid}),
            data={"role": "admin", "is_active": False, "can_reserve_orders": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        customer.refresh_from_db()
        # The flag we explicitly allow got through.
        self.assertTrue(customer.can_reserve_orders)
        # Sensitive fields did NOT.
        self.assertEqual(customer.role, "customer")
        self.assertTrue(customer.is_active)
