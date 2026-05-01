from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]
CORS_ALLOW_ALL_ORIGINS = True

# Console email so dev never accidentally hits a real SMTP server
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Show SQL while developing
LOGGING["loggers"]["django.db.backends"]["level"] = "INFO"  # noqa: F405
