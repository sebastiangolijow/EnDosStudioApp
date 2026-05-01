"""
Stripe webhook view stub.

Stripe will POST to /api/v1/payments/webhooks/stripe/ with payment events
once we configure it. The stub validates the signature and returns 200
so end-to-end testing works; real event handling lands when Order +
PaymentIntent models exist.
"""
import logging

from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .services import StripeService

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class StripeWebhookView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        signature = request.headers.get("Stripe-Signature", "")
        payload = request.body

        try:
            event = StripeService().construct_webhook_event(payload, signature)
        except Exception as e:
            logger.warning("Stripe webhook signature failed: %s", e)
            return Response({"detail": "invalid signature"}, status=status.HTTP_400_BAD_REQUEST)

        # TODO: route on event["type"] to a service handler. Day 1 stub
        # acknowledges receipt so Stripe stops retrying.
        logger.info("Stripe webhook received: type=%s id=%s", event.get("type"), event.get("id"))
        return Response({"detail": "ok"}, status=status.HTTP_200_OK)
