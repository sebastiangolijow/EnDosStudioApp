"""
Password-reset roundtrip: request → email → confirm → login with new password.

Like test_auth_roundtrip.py, this exercises the real plumbing — dj-rest-auth's
PasswordResetView + allauth's AllAuthPasswordResetForm + our custom
FrontendPasswordResetSerializer + our custom email templates — without mocks.
If this passes, customers can recover access to their accounts.
"""
import re
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core import mail
from rest_framework.test import APIClient

from tests.base import BaseTestCase

User = get_user_model()

REQUEST_URL = "/api/v1/auth/password/reset/"
CONFIRM_URL = "/api/v1/auth/password/reset/confirm/"
LOGIN_URL = "/api/v1/auth/login/"


def _extract_reset_link(body: str) -> str:
    """Pull the {FRONTEND_URL}/reset-password?... link out of an email body."""
    match = re.search(r"https?://\S*?/reset-password\?\S+", body)
    assert match, f"no reset link found in body:\n{body}"
    return match.group(0)


class PasswordResetRoundtripTests(BaseTestCase):
    def test_full_flow_request_email_confirm_login(self):
        old_password = "OldSecret123!"
        new_password = "BrandNewSecret456!"
        user = self.create_customer(email="resetme@example.com", password=old_password)

        client = APIClient()

        # 1. Request reset
        r = client.post(REQUEST_URL, {"email": user.email}, format="json")
        self.assertEqual(r.status_code, 200, r.data)

        # 2. Email was delivered, has the right structure
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, [user.email])
        self.assertIn("StickerApp", msg.subject)

        link = _extract_reset_link(msg.body)
        parsed = urlparse(link)
        params = parse_qs(parsed.query)
        self.assertIn("uid", params)
        self.assertIn("token", params)
        self.assertEqual(parsed.path, "/reset-password")
        # Frontend URL prefix is configurable — we just need it to start with
        # http(s) and not be Django's admin path
        self.assertNotIn("/admin/", link)

        uid = params["uid"][0]
        token = params["token"][0]

        # 3. Confirm with new password
        r = client.post(
            CONFIRM_URL,
            {
                "uid": uid,
                "token": token,
                "new_password1": new_password,
                "new_password2": new_password,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)

        # 4. Old password rejected; new password works
        r_old = client.post(
            LOGIN_URL,
            {"email": user.email, "password": old_password},
            format="json",
        )
        self.assertIn(r_old.status_code, (400, 401))

        r_new = client.post(
            LOGIN_URL,
            {"email": user.email, "password": new_password},
            format="json",
        )
        self.assertEqual(r_new.status_code, 200, r_new.data)
        self.assertTrue(r_new.data.get("access") or r_new.data.get("access_token"))

    def test_reset_for_unknown_email_returns_200_no_email_sent(self):
        # Don't leak whether an email is registered — same security
        # property as the register endpoint.
        client = APIClient()
        r = client.post(REQUEST_URL, {"email": "ghost@example.com"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_for_inactive_user_does_nothing(self):
        # Inactive users (e.g. registered but never set password) should not
        # receive a reset email. allauth's filter_users_by_email(is_active=True)
        # drops them; we just confirm the contract.
        User.objects.create_user(
            email="inactive@example.com",
            password="x" * 12,
            role="customer",
            is_active=False,
            is_verified=False,
        )
        client = APIClient()
        r = client.post(REQUEST_URL, {"email": "inactive@example.com"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_confirm_with_bad_token_rejected(self):
        user = self.create_customer(email="badtoken@example.com")
        client = APIClient()
        r = client.post(REQUEST_URL, {"email": user.email}, format="json")
        self.assertEqual(r.status_code, 200)
        link = _extract_reset_link(mail.outbox[0].body)
        params = parse_qs(urlparse(link).query)
        uid = params["uid"][0]

        r = client.post(
            CONFIRM_URL,
            {
                "uid": uid,
                "token": "not-the-real-token",
                "new_password1": "Whatever123!",
                "new_password2": "Whatever123!",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)

    def test_confirm_password_mismatch_rejected(self):
        user = self.create_customer(email="mismatch@example.com")
        client = APIClient()
        client.post(REQUEST_URL, {"email": user.email}, format="json")
        params = parse_qs(urlparse(_extract_reset_link(mail.outbox[0].body)).query)

        r = client.post(
            CONFIRM_URL,
            {
                "uid": params["uid"][0],
                "token": params["token"][0],
                "new_password1": "OneVersion123!",
                "new_password2": "OtherVersion456!",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 400)
