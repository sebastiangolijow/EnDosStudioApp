from django.db import models


class SoftDeleteQuerySet(models.QuerySet):
    """For models that have a deleted_at field."""

    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def deleted(self):
        return self.exclude(deleted_at__isnull=True)


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db)

    def alive(self):
        return self.get_queryset().alive()

    def deleted(self):
        return self.get_queryset().deleted()
