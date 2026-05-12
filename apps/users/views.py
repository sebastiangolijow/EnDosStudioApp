import logging

from django.db.models import Q
from rest_framework import generics, mixins, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import User
from .permissions import IsAdminOrShopStaff
from .serializers import (
    AdminUserWriteSerializer,
    RegisterSerializer,
    SetPasswordSerializer,
    UserSerializer,
)
from .services import send_verification_email

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
            phone_number=serializer.validated_data["phone_number"],
            role="customer",
            is_active=False,
            is_verified=False,
        )
        user.generate_verification_token()
        # Synchronous send. send_verification_email returns False on SMTP
        # failure but never raises; we deliberately stay returning 200 so
        # this endpoint can't be used to enumerate accounts via timing or
        # error-shape differences.
        send_verification_email(user)

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


class AdminUserViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Staff-only user management.

    Powers the /admin/users frontend page where the shop owner toggles
    `can_reserve_orders` on trusted customers. Lookup uses uuid (matches
    the rest of the API surface).

    Filters:
      - ?search=foo         icontains over email + first_name + last_name.
      - ?can_reserve_orders=true|false     boolean filter.
      - ?role=customer|shop_staff|admin    role filter.
    """

    permission_classes = [IsAdminOrShopStaff]
    queryset = User.objects.all().order_by("email")
    lookup_field = "uuid"

    def get_serializer_class(self):
        if self.action in {"update", "partial_update"}:
            return AdminUserWriteSerializer
        return UserSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        search = params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(email__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
            )
        can_reserve = params.get("can_reserve_orders")
        if can_reserve is not None:
            if can_reserve.lower() in {"true", "1", "yes"}:
                qs = qs.filter(can_reserve_orders=True)
            elif can_reserve.lower() in {"false", "0", "no"}:
                qs = qs.filter(can_reserve_orders=False)
        role = params.get("role")
        if role:
            qs = qs.filter(role=role)
        return qs

    def update(self, request, *args, **kwargs):
        """Return the full read shape after a PATCH so the admin table
        can splice the row in place without a follow-up GET."""
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(UserSerializer(serializer.instance).data)
