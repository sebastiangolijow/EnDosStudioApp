from django.urls import path

from .views import StripeWebhookView

urlpatterns = [
    path("webhooks/stripe/", StripeWebhookView.as_view(), name="stripe-webhook"),
]
