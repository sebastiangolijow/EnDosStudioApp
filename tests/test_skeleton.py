"""Smoke tests for the bootstrapped skeleton."""
from django.contrib.auth import get_user_model

from tests.base import BaseTestCase

User = get_user_model()


class SkeletonSmokeTests(BaseTestCase):
    def test_user_factory_creates_active_customer(self):
        """The default factory yields a user that can be queried back."""
        customer = self.create_customer()
        self.assertEqual(customer.role, "customer")
        self.assertTrue(customer.is_active)
        self.assertEqual(User.objects.filter(pk=customer.pk).count(), 1)

    def test_admin_factory_has_staff_and_superuser_flags(self):
        admin = self.create_admin()
        self.assertEqual(admin.role, "admin")
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)

    def test_authenticate_helper_returns_authorized_client(self):
        client, user = self.authenticate_as_customer()
        # Hit /api/v1/users/me/ — the simplest endpoint that requires auth
        response = client.get("/api/v1/users/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], user.email)
