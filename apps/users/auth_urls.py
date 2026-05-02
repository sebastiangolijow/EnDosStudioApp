from dj_rest_auth.jwt_auth import get_refresh_view
from dj_rest_auth.views import (
    LoginView,
    LogoutView,
    PasswordResetConfirmView,
    PasswordResetView,
)
from django.urls import path

from .views import RegisterView

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="rest_login"),
    path("logout/", LogoutView.as_view(), name="rest_logout"),
    path("token/refresh/", get_refresh_view().as_view(), name="token_refresh"),
    # Password reset flow:
    #   POST password/reset/         { email }                     → sends reset email
    #   POST password/reset/confirm/ { uid, token, new_password1,
    #                                  new_password2 }             → sets new password
    path("password/reset/", PasswordResetView.as_view(), name="rest_password_reset"),
    path(
        "password/reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="rest_password_reset_confirm",
    ),
]
