from .base import *  # noqa: F401,F403

DEBUG = False

# In-memory tests run faster; switch to a real Postgres test DB only if a
# specific test depends on Postgres-only features (unaccent, JSONB ops, etc.)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "test_stickerapp",
        "USER": env("POSTGRES_USER", default="stickerapp"),  # noqa: F405
        "PASSWORD": env("POSTGRES_PASSWORD", default="stickerapp"),  # noqa: F405
        "HOST": env("POSTGRES_HOST", default="db"),  # noqa: F405
        "PORT": env("POSTGRES_PORT", default="5432"),  # noqa: F405
    },
}

# Don't actually send email during tests
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Faster password hashing in tests (don't use in prod, obviously)
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
