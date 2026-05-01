import secrets
from datetime import timedelta

from django.utils import timezone

VERIFICATION_TOKEN_TTL = timedelta(hours=24)


def generate_verification_token() -> str:
    """Cryptographically random URL-safe token (~43 chars)."""
    return secrets.token_urlsafe(32)


def is_token_expired(created_at) -> bool:
    if created_at is None:
        return True
    return timezone.now() - created_at > VERIFICATION_TOKEN_TTL
