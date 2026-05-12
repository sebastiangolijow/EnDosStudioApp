"""
Base Django settings for StickerApp backend.

Each environment (dev, prod, test) imports from this file and overrides
the bits it cares about. Anything that's the same across environments
lives here.

Conventions (see CLAUDE.md):
- Single-tenant. No lab_client_id-style scaffolding.
- UUID PKs everywhere via apps.core.models.BaseModel.
- Custom User in apps.users.models with email as USERNAME_FIELD.
- Service-layer pattern: business logic in apps/<app>/services.py.
- Stripe is the only payment gateway; not gateway-agnostic.
"""
import os
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env", overwrite=False)

# === Core ===
SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-dev-only-replace-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

ADMIN_URL = env("ADMIN_URL", default="django-admin/")

# === Apps ===
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
    "allauth",
    "allauth.account",
    "dj_rest_auth",
    "dj_rest_auth.registration",
    "simple_history",
]

LOCAL_APPS = [
    "apps.core",
    "apps.users",
    "apps.orders",
    "apps.payments",
    "apps.products",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

SITE_ID = 1

# === Middleware ===
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# === Database ===
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://stickerapp:stickerapp@db:5432/stickerapp",
    ),
}

# === Auth ===
AUTH_USER_MODEL = "users.User"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# allauth settings — email is the identifier, no usernames anywhere
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_UNIQUE_EMAIL = True

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# === DRF ===
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    # StandardPageNumberPagination supports ?page_size=N (capped at 100).
    # ViewSets that need a different default still set their own paginator.
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPageNumberPagination",
    "PAGE_SIZE": 20,
    # django-filter + DRF's built-in search + ordering filters wired
    # globally. Individual ViewSets opt into them by setting
    # filterset_class / filterset_fields / search_fields / ordering_fields.
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "USER_ID_FIELD": "uuid",        # custom User uses uuid, not id
    "USER_ID_CLAIM": "user_id",
}

REST_AUTH = {
    "USE_JWT": True,
    "JWT_AUTH_HTTPONLY": False,
    "TOKEN_MODEL": None,  # JWT only — disable the legacy Token model dj-rest-auth 7.x defaults to
    # Custom serializer that builds password-reset URLs pointing at the
    # frontend SPA, not Django admin. See apps/users/password_reset.py.
    "PASSWORD_RESET_SERIALIZER": "apps.users.password_reset.FrontendPasswordResetSerializer",
}

# === CORS ===
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True

# === I18N ===
LANGUAGE_CODE = "es"   # Barcelona — default to Spanish; switchable per-user
TIME_ZONE = "Europe/Madrid"
USE_I18N = True
USE_TZ = True

# === Static / Media ===
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# === Email ===
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@stickerapp.local")
# Recipient for new-order notifications. Falls back to DEFAULT_FROM_EMAIL
# so dev / test environments don't fail loud on empty config.
SHOP_OWNER_EMAIL = env("SHOP_OWNER_EMAIL", default=DEFAULT_FROM_EMAIL)

# === Frontend ===
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:5173")

# === Stripe ===
STRIPE_PUBLISHABLE_KEY = env("STRIPE_PUBLISHABLE_KEY", default="")
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY", default="")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET", default="")

# === Misc ===
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"   # for Django built-ins; our models use UUID PKs explicitly

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django.db.backends": {
            "level": "WARNING",  # bump to DEBUG locally to see SQL
        },
    },
}
