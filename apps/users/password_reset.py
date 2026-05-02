"""
Password reset customizations.

dj-rest-auth + allauth ship a working password-reset flow out of the box,
but two things need to be replaced for our setup:

  1. The reset URL — by default it points at Django admin's password
     reset confirm view, which doesn't exist in our SPA architecture.
     We swap it for `{FRONTEND_URL}/reset-password?uid=...&token=...`
     so the frontend handles the form.

  2. The email content — allauth's stock template is plain-text English.
     We override the `account/email/password_reset_key*` templates with
     a branded Spanish version (see templates/account/email/).

The pattern follows labcontrol/apps/users where dj-rest-auth's defaults
are reused but URL generation is customized for SPA flows.
"""
from allauth.account.utils import user_pk_to_url_str
from dj_rest_auth.serializers import PasswordResetSerializer
from django.conf import settings


def frontend_url_generator(request, user, temp_key) -> str:
    """Build the URL the customer clicks in the reset email.

    Signature mirrors allauth's `default_url_generator`. The frontend
    expects `?uid=...&token=...` query params — same shape used by
    dj-rest-auth's `password/reset/confirm/` endpoint, so the frontend
    can simply forward them after the customer types a new password.
    """
    uid = user_pk_to_url_str(user)
    base = settings.FRONTEND_URL or "http://localhost:5173"
    return f"{base}/reset-password?uid={uid}&token={temp_key}"


class FrontendPasswordResetSerializer(PasswordResetSerializer):
    """dj-rest-auth's PasswordResetSerializer with our frontend URL injected.

    Wired via REST_AUTH['PASSWORD_RESET_SERIALIZER'] in settings/base.py.
    """

    def get_email_options(self):
        return {
            "url_generator": frontend_url_generator,
        }
