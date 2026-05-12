"""Category routes — mounted at /api/v1/categories/.

Read-only list/detail for the admin form's autosuggest. Categories are
created implicitly when an admin types a new name via ProductWriteSerializer.
"""
from rest_framework.routers import DefaultRouter

from .views import CategoryViewSet


router = DefaultRouter()
router.register(r"", CategoryViewSet, basename="category")

urlpatterns = router.urls
