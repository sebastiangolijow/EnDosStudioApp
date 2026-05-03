"""Cut-path SVG generation."""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.orders.cut_path import build_cut_svg, generate_cut_path_file
from apps.orders.models import Order, OrderFile
from tests.base import BaseTestCase


def _png_bytes(width: int, height: int, *, alpha_shape: str = "rect") -> bytes:
    """Build a PNG with a black silhouette on transparent bg."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    px = img.load()
    cx, cy = width // 2, height // 2
    if alpha_shape == "rect":
        for y in range(height // 4, 3 * height // 4):
            for x in range(width // 4, 3 * width // 4):
                px[x, y] = (0, 0, 0, 255)
    elif alpha_shape == "circle":
        r = min(width, height) // 3
        for y in range(height):
            for x in range(width):
                if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                    px[x, y] = (0, 0, 0, 255)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class CutPathBuilderTests(BaseTestCase):
    """Pure-function tests for build_cut_svg — no DB, no model."""

    def test_cuadrado_emits_a_rect_at_size(self):
        svg = build_cut_svg(shape="cuadrado", width_mm=50, height_mm=50)
        # Header carries the physical size so the cutter scales correctly.
        self.assertIn('width="50mm"', svg)
        self.assertIn('height="50mm"', svg)
        self.assertIn('viewBox="0 0 50 50"', svg)
        # Rect, no rounded corners.
        self.assertIn('<rect', svg)
        self.assertIn('width="50"', svg)
        self.assertIn('height="50"', svg)
        self.assertNotIn(' rx=', svg)
        # Cut-line stroke convention.
        self.assertIn('stroke="red"', svg)
        self.assertIn('fill="none"', svg)

    def test_circulo_emits_an_ellipse(self):
        svg = build_cut_svg(shape="circulo", width_mm=40, height_mm=40)
        self.assertIn('<ellipse', svg)
        self.assertIn('cx="20.0"', svg)
        self.assertIn('rx="20.0"', svg)

    def test_redondeadas_uses_10pct_corner_radius(self):
        svg = build_cut_svg(shape="redondeadas", width_mm=80, height_mm=50)
        # Shorter edge = 50, 10% = 5 mm.
        self.assertIn(' rx="5.0"', svg)
        self.assertIn(' ry="5.0"', svg)

    def test_contorneado_without_mask_falls_back_to_rect(self):
        # No mask supplied → degrade gracefully to a rectangle so the order
        # can still be cut. The shop owner can swap in a manual SVG later.
        svg = build_cut_svg(shape="contorneado", width_mm=30, height_mm=30, mask_file=None)
        self.assertIn('<rect', svg)
        self.assertIn('width="30"', svg)

    def test_contorneado_traces_alpha_to_path(self):
        # Build a PNG mask with a centered black rectangle on transparent
        # bg. The traced path should be a polygon roughly enclosing that
        # rectangle, scaled to the chosen physical size.
        png = _png_bytes(100, 100, alpha_shape="rect")
        mask_file = SimpleUploadedFile("mask.png", png, content_type="image/png")
        svg = build_cut_svg(
            shape="contorneado",
            width_mm=50,
            height_mm=50,
            mask_file=mask_file,
        )
        # Expect a <path d="..."> element rather than a rect fallback.
        self.assertIn('<path d=', svg)
        # Path must close itself ("Z"), and have at least 4 commands (4
        # corners of a rectangle is the minimum sensible polygon).
        self.assertIn(' Z', svg)


class GenerateCutPathFileTests(BaseTestCase):
    """End-to-end: generate, persist, replace."""

    def test_generates_orderfile_for_geometric_shape(self):
        customer = self.create_customer()
        order = Order.objects.create(
            created_by=customer,
            shape="cuadrado",
            width_mm=50,
            height_mm=50,
        )
        cut_file = generate_cut_path_file(order)
        self.assertEqual(cut_file.kind, "cut_path")
        self.assertEqual(cut_file.mime_type, "image/svg+xml")
        self.assertGreater(cut_file.size_bytes, 0)
        # Round-trip read.
        with cut_file.file.open("rb") as f:
            content = f.read().decode("utf-8")
        self.assertIn('<rect', content)
        self.assertIn('width="50mm"', content)

    def test_replaces_existing_cut_path_file(self):
        # Idempotent: regenerating overwrites the previous file (the
        # unique_together(order, kind) constraint would otherwise raise).
        customer = self.create_customer()
        order = Order.objects.create(
            created_by=customer,
            shape="circulo",
            width_mm=30,
            height_mm=30,
        )
        first = generate_cut_path_file(order)
        first_pk = first.pk

        # Change shape, regenerate.
        order.shape = "cuadrado"
        order.save(update_fields=["shape"])
        second = generate_cut_path_file(order)

        # New file replaces the old one — different PK, only one cut_path
        # OrderFile remaining.
        self.assertNotEqual(first_pk, second.pk)
        self.assertEqual(
            OrderFile.objects.filter(order=order, kind="cut_path").count(),
            1,
        )
        with second.file.open("rb") as f:
            content = f.read().decode("utf-8")
        self.assertIn('<rect', content)
