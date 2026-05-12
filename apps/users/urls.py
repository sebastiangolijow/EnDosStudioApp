from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import AdminUserViewSet, CurrentUserView, SetPasswordView

# Staff-only user management — mounted at /api/v1/users/. The router's
# detail routes share the prefix with /me/ + /set-password/ but DRF's
# default router only registers verbs that match the registered viewset
# actions, so the two coexist.
router = DefaultRouter()
router.register(r"", AdminUserViewSet, basename="admin-user")

urlpatterns = [
    path("me/", CurrentUserView.as_view(), name="current-user"),
    path("set-password/", SetPasswordView.as_view(), name="set-password"),
    path("", include(router.urls)),
]
