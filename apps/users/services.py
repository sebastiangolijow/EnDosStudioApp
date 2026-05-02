"""User-side business logic.

Currently exposes the verification email service only. Synchronous send —
no Celery on day 1. If a real notification surface emerges (multi-channel,
templated transactionals, scheduling), promote this to apps/notifications/.
"""
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def _plain_text_body(*, user_name: str, setup_url: str) -> str:
    """Hand-written plain-text fallback. Don't strip_tags() the HTML body —
    the inline <style> block survives strip_tags and pollutes the output."""
    return (
        f"Hola {user_name},\n\n"
        "Para activar tu cuenta en StickerApp, configurá tu contraseña "
        "haciendo clic en el siguiente enlace:\n\n"
        f"{setup_url}\n\n"
        "Si no creaste una cuenta, ignorá este mensaje.\n\n"
        "— StickerApp\n"
    )


def send_verification_email(user) -> bool:
    """Send the password-setup link to a freshly registered user.

    The user typed a password at /auth/register/, but allauth + StickerApp's
    flow requires them to verify by clicking an emailed link and (re-)setting
    a password at /api/v1/users/set-password/. That is the only path that
    creates the allauth EmailAddress row needed for login to succeed.

    Returns True on success, False on any failure. Logs `user.pk` (UUID) for
    correlation; emails are PII — don't log them.

    Caller (RegisterView) MUST stay returning 200 regardless of the boolean
    so the endpoint doesn't leak whether the email succeeded.
    """
    if user.is_verified:
        logger.info("send_verification_email: user pk=%s already verified, skipping", user.pk)
        return False

    if not user.verification_token:
        logger.error(
            "send_verification_email: user pk=%s has no verification_token; "
            "did the caller forget generate_verification_token()?",
            user.pk,
        )
        return False

    frontend_url = settings.FRONTEND_URL or "http://localhost:5173"
    setup_url = (
        f"{frontend_url}/set-password"
        f"?token={user.verification_token}"
        f"&email={user.email}"
    )

    context = {
        "user_name": user.get_full_name() or user.email,
        "password_setup_url": setup_url,
    }

    try:
        html_body = render_to_string("emails/password_setup.html", context)
        text_body = _plain_text_body(
            user_name=context["user_name"],
            setup_url=setup_url,
        )

        message = EmailMultiAlternatives(
            subject="Confirmá tu cuenta en StickerApp",
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        message.attach_alternative(html_body, "text/html")
        message.send(fail_silently=False)
    except Exception:
        logger.exception("send_verification_email: failed for user pk=%s", user.pk)
        return False

    logger.info("Verification email sent to user pk=%s", user.pk)
    return True
