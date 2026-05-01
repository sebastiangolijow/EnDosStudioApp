"""
Base model mixins.

All domain models inherit BaseModel for UUID PK + timestamps + created_by.
The mixins are kept separate so a model that doesn't need created_by
(e.g. a payment record where the actor is implied) can use TimeStampedModel
+ UUIDModel without dragging the FK in.
"""
import uuid

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    """created_at + updated_at, set automatically."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    """UUID primary key. Always use .pk; .id raises AttributeError."""

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class CreatedByModel(models.Model):
    """Tracks which user created the record. SET_NULL so user-deletes don't cascade."""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",   # no reverse — keeps user.created_<thing>_set off the User model
    )

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TimeStampedModel, CreatedByModel):
    """The default base for domain models."""

    class Meta:
        abstract = True
