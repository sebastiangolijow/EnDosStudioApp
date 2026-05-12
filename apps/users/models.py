"""
Custom User model for StickerApp.

- UUID primary key (matches the project-wide convention; .pk works, .id raises)
- Email is the unique identifier (USERNAME_FIELD = "email"). No username column.
- Three roles: admin, shop_staff, customer.
- Verification flow: imported/admin-created users start is_active=False,
  is_verified=False, with a verification_token. The set-password endpoint
  flips both flags AND creates the allauth EmailAddress row that's required
  for login to actually work.

The allauth EmailAddress trap:
  django-allauth authenticates against EmailAddress, not User.email. A user
  with User.email set + correct password but no matching EmailAddress row
  silently fails to log in ("no user found"). The verify_email() helper
  below creates that row; SetPasswordView calls it.
"""
import uuid

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from simple_history.models import HistoricalRecords

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    """Custom user with email as primary identifier and a role field."""

    ROLE_CHOICES = [
        ("admin", _("Administrator")),
        ("shop_staff", _("Shop Staff")),
        ("customer", _("Customer")),
    ]
    LANGUAGE_CHOICES = [
        ("ES", _("Español")),
        ("EN", _("English")),
        ("CA", _("Català")),
    ]

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Identity
    email = models.EmailField(_("email"), unique=True)
    first_name = models.CharField(_("first name"), max_length=50, blank=True)
    last_name = models.CharField(_("last name"), max_length=50, blank=True)
    phone_number = models.CharField(_("phone number"), max_length=50, blank=True)
    profile_picture = models.ImageField(
        _("profile picture"),
        upload_to="profile_pictures/",
        null=True,
        blank=True,
    )
    language = models.CharField(
        _("preferred language"),
        max_length=2,
        choices=LANGUAGE_CHOICES,
        default="ES",
    )

    role = models.CharField(
        _("role"),
        max_length=20,
        choices=ROLE_CHOICES,
        default="customer",
    )

    # Account state
    is_active = models.BooleanField(_("is active"), default=False)
    is_staff = models.BooleanField(_("is staff"), default=False)
    is_verified = models.BooleanField(_("email verified"), default=False)

    # Whitelist gate for in-store pickup reservations. Off by default
    # — only customers the shop owner explicitly trusts (cash regulars)
    # get the Reserve CTA at checkout. Managed via the admin users page.
    can_reserve_orders = models.BooleanField(
        _("can reserve orders"), default=False
    )

    # Email verification token (for set-password / verify flows)
    verification_token = models.CharField(max_length=100, blank=True)
    verification_token_created_at = models.DateTimeField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)
    updated_at = models.DateTimeField(_("updated at"), auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # Audit trail
    history = HistoricalRecords(
        history_user_id_field=models.UUIDField(null=True, blank=True),
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "users_user"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["role", "is_active"]),
        ]

    def __str__(self):
        return self.email or str(self.pk)

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self):
        return self.first_name or self.email

    # --- Verification helpers ---
    def generate_verification_token(self) -> str:
        """Generate + persist a fresh verification token. Returns the token."""
        from .tokens import generate_verification_token

        self.verification_token = generate_verification_token()
        self.verification_token_created_at = timezone.now()
        self.save(update_fields=["verification_token", "verification_token_created_at"])
        return self.verification_token

    def is_verification_token_valid(self) -> bool:
        from .tokens import is_token_expired

        if not self.verification_token or not self.verification_token_created_at:
            return False
        return not is_token_expired(self.verification_token_created_at)

    def verify_email(self):
        """
        Mark the user as email-verified AND create the allauth EmailAddress
        row that's required for login to work.

        Without the EmailAddress row, allauth.account.AuthenticationBackend
        can't find the user during login — even though User.email is set —
        and login silently fails. This is the trap.

        Call this from the SetPasswordView (or any flow that activates the
        user). Don't call it twice — update_or_create handles re-runs.
        """
        from allauth.account.models import EmailAddress

        self.is_verified = True
        self.verification_token = ""
        self.verification_token_created_at = None
        self.save(
            update_fields=[
                "is_verified",
                "verification_token",
                "verification_token_created_at",
            ]
        )

        EmailAddress.objects.update_or_create(
            user=self,
            email=self.email.lower(),
            defaults={"verified": True, "primary": True},
        )

    def soft_delete(self):
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_active", "deleted_at"])

    def restore(self):
        self.is_active = True
        self.deleted_at = None
        self.save(update_fields=["is_active", "deleted_at"])
