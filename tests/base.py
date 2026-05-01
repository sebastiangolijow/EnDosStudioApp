"""
Base test case with factory methods.

Inherit from BaseTestCase to get common helpers without each test
re-implementing user creation, authentication, etc. The factories are
deliberate — they encode the project's conventions (UUID PK access via .pk,
allauth EmailAddress row creation, role values) so tests don't accidentally
drift from how production code creates users.
"""
import uuid

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


class BaseTestCase(APITestCase):
    """
    Common base for API tests.

    All factory methods return SAVED objects with sensible defaults; pass
    kwargs to override anything. The auth helpers issue a JWT and return
    an authenticated APIClient + the user.
    """

    # === User factories ===

    def create_user(
        self,
        email: str | None = None,
        password: str = "TestPass123!",
        role: str = "customer",
        is_active: bool = True,
        is_verified: bool = True,
        create_email_address: bool = True,
        **extra,
    ) -> User:
        """
        Create a saved User with sensible defaults.

        is_active=True + create_email_address=True by default because most
        tests want a user that can log in. Set is_active=False (and the
        flag) when testing the activation flow itself.
        """
        if email is None:
            email = f"user-{uuid.uuid4().hex[:8]}@example.com"

        user = User.objects.create_user(
            email=email,
            password=password,
            role=role,
            is_active=is_active,
            is_verified=is_verified,
            **extra,
        )

        # The allauth EmailAddress trap: tests that exercise login fail
        # silently without this row even when User.email is set. Default
        # to creating it so the common case "active user logs in" just works.
        if create_email_address:
            EmailAddress.objects.create(
                user=user,
                email=user.email.lower(),
                primary=True,
                verified=is_verified,
            )

        return user

    def create_admin(self, **extra) -> User:
        return self.create_user(
            role="admin",
            is_staff=True,
            is_superuser=True,
            **extra,
        )

    def create_shop_staff(self, **extra) -> User:
        return self.create_user(role="shop_staff", **extra)

    def create_customer(self, **extra) -> User:
        return self.create_user(role="customer", **extra)

    # === Authentication helpers ===

    def authenticate(self, user: User) -> APIClient:
        """Return an APIClient authenticated as the given user."""
        refresh = RefreshToken.for_user(user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        return client

    def authenticate_as_admin(self) -> tuple[APIClient, User]:
        user = self.create_admin()
        return self.authenticate(user), user

    def authenticate_as_shop_staff(self) -> tuple[APIClient, User]:
        user = self.create_shop_staff()
        return self.authenticate(user), user

    def authenticate_as_customer(self) -> tuple[APIClient, User]:
        user = self.create_customer()
        return self.authenticate(user), user
