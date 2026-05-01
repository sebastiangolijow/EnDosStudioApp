import logging

from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import User
from .permissions import IsAdminOrShopStaff
from .serializers import RegisterSerializer, SetPasswordSerializer, UserSerializer

logger = logging.getLogger(__name__)


class CurrentUserView(generics.RetrieveUpdateAPIView):
    """GET /api/v1/users/me/ — view + update the logged-in user's profile."""

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class RegisterView(APIView):
    """POST /api/v1/auth/register/ — customer self-registration.

    Creates an inactive customer + verification token, then queues the
    password-setup email. The user can't log in until they click the link.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"].lower()
        if User.objects.filter(email=email).exists():
            # Don't leak which emails are registered. Return success but
            # send no email (or send a "you already have an account" email).
            return Response(
                {"detail": "If the email is valid, a setup link was sent."},
                status=status.HTTP_200_OK,
            )

        user = User.objects.create_user(
            email=email,
            password=serializer.validated_data["password"],
            first_name=serializer.validated_data.get("first_name", ""),
            last_name=serializer.validated_data.get("last_name", ""),
            role="customer",
            is_active=False,
            is_verified=False,
        )
        user.generate_verification_token()

        # TODO: queue the verification email here. The Celery task scaffolding
        # isn't in this skeleton (no Celery on day 1) — for now, log a link
        # for local development. When SMTP + a real notification path lands,
        # wire it through apps.users.services or apps.notifications.
        logger.info(
            "Registration: user pk=%s — verification link "
            "/set-password?token=%s&email=%s",
            user.pk,
            user.verification_token,
            user.email,
        )

        return Response(
            {"detail": "If the email is valid, a setup link was sent."},
            status=status.HTTP_200_OK,
        )


class SetPasswordView(APIView):
    """POST /api/v1/users/set-password/

    The user clicks the email link, lands on the frontend with `email` and
    `token` in the URL, types a password, and the frontend posts here. We:

      1. Validate the token + expiry.
      2. Set the password.
      3. Activate the user (is_active=True).
      4. Mark verified + create the allauth EmailAddress row.

    Without step 4, login silently fails. See User.verify_email() for the
    explanation.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"].lower()
        token = serializer.validated_data["token"]
        password = serializer.validated_data["password"]

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response(
                {"detail": "Invalid token or email."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user.verification_token != token:
            return Response(
                {"detail": "Invalid token or email."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not user.is_verification_token_valid():
            return Response(
                {"detail": "Token expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(password)
        user.is_active = True
        user.save(update_fields=["password", "is_active"])
        user.verify_email()  # also creates the allauth EmailAddress row

        return Response(
            {"detail": "Password set. You can now log in.", "user": UserSerializer(user).data},
            status=status.HTTP_200_OK,
        )
