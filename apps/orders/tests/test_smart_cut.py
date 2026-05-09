"""Smart-cut (rembg AI background removal) tests.

The actual rembg model (~170 MB ONNX) is mocked at the `rembg.remove`
import inside the service module — tests don't load the real model so
they stay fast (sub-second) and don't need the model file present.

Coverage:
  - happy path: white-square mock → returns kind=ok with ≥ 3 points
  - no-original-file → NoOriginalFile / 400 from the view
  - empty mask (mock returns fully transparent) → kind=no-contour-found
    + HTTP 200 (an empty result is not an error)
  - rembg failure → SmartCutModelUnavailable / 503 from the view
  - cross-customer access → 404 (ownership enforced by get_queryset)
"""
from io import BytesIO
from unittest import mock

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from apps.orders.models import Order, OrderFile
from apps.orders.services_smart_cut import (
    NoOriginalFile,
    SmartCutModelUnavailable,
    smart_cut_for_order,
)
from tests.base import BaseTestCase


def _png_bytes(color=(180, 180, 180), size=(64, 64)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _mock_rembg_white_square(size=(64, 64)):
    """Build an RGBA PIL image with an opaque white square in the middle.

    Used to mock `rembg.remove`'s output so the service has a non-trivial
    binary alpha to walk. The white square sits inset by 8 px so the
    contour is well away from the canvas edge.
    """
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    pixels = img.load()
    margin = 8
    for y in range(margin, size[1] - margin):
        for x in range(margin, size[0] - margin):
            pixels[x, y] = (255, 255, 255, 255)
    return img


def _mock_rembg_transparent(size=(64, 64)):
    """Build a fully transparent RGBA — simulates rembg returning no foreground."""
    return Image.new("RGBA", size, (0, 0, 0, 0))


def _seed_order_with_original(self, customer, color=(180, 180, 180)):
    """Helper: create a draft order + upload an `original` PNG file."""
    order = Order.objects.create(created_by=customer)
    OrderFile.objects.create(
        order=order,
        kind="original",
        file=SimpleUploadedFile(
            "test.png", _png_bytes(color=color), content_type="image/png"
        ),
        created_by=customer,
    )
    return order


class SmartCutServiceTests(BaseTestCase):
    """Service-level tests, no HTTP."""

    def test_returns_polygon_for_white_square_mask(self):
        _, customer = self.authenticate_as_customer()
        order = _seed_order_with_original(self, customer)

        with mock.patch(
            "apps.orders.services_smart_cut.remove",
            return_value=_mock_rembg_white_square(),
        ):
            result = smart_cut_for_order(order)

        self.assertEqual(result["kind"], "ok")
        self.assertGreaterEqual(len(result["points"]), 3)
        # Every point is image-space with integer coords.
        for pt in result["points"]:
            self.assertEqual(pt["kind"], "image")
            self.assertIsInstance(pt["x"], int)
            self.assertIsInstance(pt["y"], int)
        # artwork_points mirrors points in this version.
        self.assertEqual(result["artwork_points"], result["points"])
        self.assertGreater(result["area_px"], 0)
        # Cleaned RGBA inline as a data URL.
        self.assertTrue(
            result["cleaned_image_data_url"].startswith("data:image/png;base64,")
        )

    def test_no_original_file_raises_NoOriginalFile(self):
        _, customer = self.authenticate_as_customer()
        order = Order.objects.create(created_by=customer)  # no file

        with self.assertRaises(NoOriginalFile):
            smart_cut_for_order(order)

    def test_no_contour_returns_kind_no_contour_found(self):
        _, customer = self.authenticate_as_customer()
        order = _seed_order_with_original(self, customer)

        with mock.patch(
            "apps.orders.services_smart_cut.remove",
            return_value=_mock_rembg_transparent(),
        ):
            result = smart_cut_for_order(order)

        self.assertEqual(result["kind"], "no-contour-found")
        self.assertEqual(result["points"], [])
        self.assertEqual(result["artwork_points"], [])
        self.assertEqual(result["area_px"], 0)
        self.assertIsNone(result["cleaned_image_data_url"])

    def test_rembg_inference_failure_raises_SmartCutModelUnavailable(self):
        _, customer = self.authenticate_as_customer()
        order = _seed_order_with_original(self, customer)

        with mock.patch(
            "apps.orders.services_smart_cut.remove",
            side_effect=RuntimeError("ONNX runtime crashed"),
        ):
            with self.assertRaises(SmartCutModelUnavailable):
                smart_cut_for_order(order)


class SmartCutEndpointTests(BaseTestCase):
    """HTTP-level tests for POST /api/v1/orders/{uuid}/smart-cut/."""

    def test_returns_200_with_polygon_on_happy_path(self):
        client, customer = self.authenticate_as_customer()
        order = _seed_order_with_original(self, customer)

        with mock.patch(
            "apps.orders.services_smart_cut.remove",
            return_value=_mock_rembg_white_square(),
        ):
            response = client.post(
                reverse("order-smart-cut", kwargs={"pk": order.pk}),
            )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["kind"], "ok")
        self.assertGreaterEqual(len(response.data["points"]), 3)
        self.assertGreater(response.data["area_px"], 0)

    def test_returns_400_when_no_original_file(self):
        client, customer = self.authenticate_as_customer()
        order = Order.objects.create(created_by=customer)

        response = client.post(
            reverse("order-smart-cut", kwargs={"pk": order.pk}),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.data)

    def test_returns_200_with_no_contour_kind_when_mask_empty(self):
        client, customer = self.authenticate_as_customer()
        order = _seed_order_with_original(self, customer)

        with mock.patch(
            "apps.orders.services_smart_cut.remove",
            return_value=_mock_rembg_transparent(),
        ):
            response = client.post(
                reverse("order-smart-cut", kwargs={"pk": order.pk}),
            )

        # Empty result is not an error — the customer just gets a toast.
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["kind"], "no-contour-found")
        self.assertEqual(response.data["points"], [])

    def test_returns_503_when_model_unavailable(self):
        client, customer = self.authenticate_as_customer()
        order = _seed_order_with_original(self, customer)

        with mock.patch(
            "apps.orders.services_smart_cut.remove",
            side_effect=RuntimeError("ONNX runtime crashed"),
        ):
            response = client.post(
                reverse("order-smart-cut", kwargs={"pk": order.pk}),
            )

        self.assertEqual(response.status_code, 503)
        self.assertIn("detail", response.data)

    def test_returns_404_when_other_customer_tries(self):
        # Customer A creates the order; customer B can't access it.
        _, customer_a = self.authenticate_as_customer()
        order = _seed_order_with_original(self, customer_a)

        client_b, _ = self.authenticate_as_customer()
        response = client_b.post(
            reverse("order-smart-cut", kwargs={"pk": order.pk}),
        )

        self.assertEqual(response.status_code, 404)
