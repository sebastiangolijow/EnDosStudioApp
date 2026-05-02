"""
M2 gate test: full registration → set-password → login → /me/ roundtrip.

Per NEXT_SESSION.md, if this passes, the auth foundation is solid:
the allauth EmailAddress trap is correctly handled by SetPasswordView,
the JWT pipeline issues a usable token, and the authenticated /me/ endpoint
returns the right user.

If this test breaks, do NOT 'fix' it by mocking around the EmailAddress
creation — that defeats the whole point. Read SetPasswordView and
User.verify_email() and find the real divergence.
"""
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from tests.base import BaseTestCase

User = get_user_model()


REGISTER_URL = "/api/v1/auth/register/"
SET_PASSWORD_URL = "/api/v1/users/set-password/"
LOGIN_URL = "/api/v1/auth/login/"
ME_URL = "/api/v1/users/me/"


class AuthRoundtripTests(BaseTestCase):
    """The whole registration → first login flow, end-to-end, no shortcuts."""

    def test_register_set_password_login_me(self):
        email = "newcustomer@example.com"
        initial_password = "InitialPass123!"
        final_password = "FinalSecurePass456!"

        # 1. Register — anonymous, creates inactive customer + verification_token
        client = APIClient()
        r = client.post(
            REGISTER_URL,
            data={
                "email": email,
                "password": initial_password,
                "first_name": "New",
                "last_name": "Customer",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)

        user = User.objects.get(email=email)
        self.assertFalse(user.is_active, "registered user should be inactive until set-password")
        self.assertFalse(user.is_verified)
        self.assertTrue(user.verification_token, "register should generate a token")
        self.assertFalse(
            EmailAddress.objects.filter(user=user).exists(),
            "EmailAddress row must NOT exist yet — only set-password creates it",
        )

        # 2. Login attempt before set-password — must fail (user inactive + no EmailAddress)
        before = client.post(
            LOGIN_URL,
            data={"email": email, "password": initial_password},
            format="json",
        )
        self.assertIn(
            before.status_code,
            (400, 401),
            f"login before set-password must fail; got {before.status_code}",
        )

        # 3. Set password — using the token pulled from the DB (in real life,
        #    the customer clicks the email link with this token in the URL)
        r = client.post(
            SET_PASSWORD_URL,
            data={
                "email": email,
                "token": user.verification_token,
                "password": final_password,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)

        user.refresh_from_db()
        self.assertTrue(user.is_active, "set-password must activate the user")
        self.assertTrue(user.is_verified)
        self.assertEqual(user.verification_token, "", "token must be cleared after use")

        # The allauth EmailAddress trap: this row MUST exist or login will
        # silently fail with no obvious cause.
        ea = EmailAddress.objects.get(user=user)
        self.assertEqual(ea.email, email.lower())
        self.assertTrue(ea.verified)
        self.assertTrue(ea.primary)

        # 4. Login with the new password — should succeed and return JWTs
        r = client.post(
            LOGIN_URL,
            data={"email": email, "password": final_password},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        access = r.data.get("access") or r.data.get("access_token")
        self.assertTrue(access, f"login must return a JWT access token; got {r.data}")

        # 5. Hit /me/ with the bearer token — confirm the JWT actually works
        auth_client = APIClient()
        auth_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        r = auth_client.get(ME_URL)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["email"], email)
        self.assertEqual(r.data["role"], "customer")
        self.assertEqual(r.data["first_name"], "New")

        # 6. Old (initial) password must NOT work anymore — set-password replaced it
        r = client.post(
            LOGIN_URL,
            data={"email": email, "password": initial_password},
            format="json",
        )
        self.assertIn(
            r.status_code, (400, 401),
            "the original registration password must not work after set-password",
        )

    def test_set_password_with_invalid_token_rejected(self):
        client = APIClient()
        client.post(
            REGISTER_URL,
            data={"email": "tok@example.com", "password": "x" * 12},
            format="json",
        )
        r = client.post(
            SET_PASSWORD_URL,
            data={
                "email": "tok@example.com",
                "token": "not-the-real-token",
                "password": "Whatever123!",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)
        user = User.objects.get(email="tok@example.com")
        self.assertFalse(user.is_active, "user must stay inactive after a bad token")
        self.assertFalse(EmailAddress.objects.filter(user=user).exists())

    def test_register_does_not_leak_existing_email(self):
        # Pre-create a user
        existing = self.create_customer(email="existing@example.com")
        self.assertTrue(existing.is_active)

        client = APIClient()
        r = client.post(
            REGISTER_URL,
            data={"email": "existing@example.com", "password": "SomePass123!"},
            format="json",
        )
        # Per RegisterView: returns 200 with the same generic message even if
        # the email is already taken — security feature, don't leak account existence.
        self.assertEqual(r.status_code, 200)

    def test_login_path_uses_email_address_lookup(self):
        # An active user with a working EmailAddress row (the BaseTestCase
        # factory creates one by default) MUST be able to log in.
        password = "WorksFine123!"
        user = self.create_customer(email="works@example.com", password=password)
        self.assertTrue(EmailAddress.objects.filter(user=user, verified=True).exists())

        client = APIClient()
        r = client.post(
            LOGIN_URL,
            data={"email": "works@example.com", "password": password},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data.get("access") or r.data.get("access_token"))
