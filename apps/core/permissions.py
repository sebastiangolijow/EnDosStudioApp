"""Project-wide DRF permission classes.

The actual classes live in apps.users.permissions because they reason
about user.role; this module just re-exports them so other apps can
import from a stable, app-neutral path.
"""
from apps.users.permissions import (  # noqa: F401
    IsAdmin,
    IsAdminOrShopStaff,
    IsCustomerOwner,
)
