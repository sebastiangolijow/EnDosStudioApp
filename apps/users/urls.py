from django.urls import path

from .views import CurrentUserView, SetPasswordView

urlpatterns = [
    path("me/", CurrentUserView.as_view(), name="current-user"),
    path("set-password/", SetPasswordView.as_view(), name="set-password"),
]
