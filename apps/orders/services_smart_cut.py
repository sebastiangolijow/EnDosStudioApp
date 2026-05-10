"""
"Recorte inteligente" — AI background removal for the editor.

The editor's classical OpenCV.js auto-cut handles ~80% of customer images
fine but fails on artwork colors that overlap with the background, busy
backgrounds, and isolated multi-piece designs. This module wraps `rembg`
(isnet-general-use ONNX model, ~170 MB) to provide a cleaner foreground
silhouette as an opt-in upgrade path.

Pipeline (sync, on the request thread; see CLAUDE.md "Smart cut (rembg)"):
  1. Open the order's `original` OrderFile with Pillow.
  2. rembg.remove(img, session=isnet-general-use) → foreground RGBA.
  3. Threshold the alpha channel at 128 to get a binary silhouette.
  4. Morphological opening (MinFilter then MaxFilter, kernel 13) to drop
     thin appendages (single-pixel bridges between the main silhouette
     and decorative bits) that would otherwise become curving outward
     "tendrils" once we dilate.
  5. Bleed-margin dilation: MaxFilter with a kernel sized from the caller-
     supplied `margin_mm` and the image's px-per-mm. Produces a clean
     simple-polygon expansion at any margin (something normal-bisector
     offset on the frontend cannot do for non-convex shapes).
  6. Walk the boundary with apps.orders.cut_path._walk_alpha_contour.
  7. Drop colinear runs (cheap O(n) post-pass — keeps ≥ 3 pts).
  8. Return JSON dict + a base64 PNG `cleaned_image_data_url` whose
     bleed ring carries the ORIGINAL RGB pixels (so the customer sees
     the artwork's surrounding color extending outward, not transparent
     truncation).

Why backend dilation (and not frontend `offsetPolygonOutward`): the JS
normal-bisector offset is mathematically incorrect for non-convex
polygons — sharp concavities self-intersect, producing visible polygon
fragmentation at large margins. PIL's MaxFilter implements proper
Minkowski-sum dilation on the binary mask, which always yields a simple
polygon. The frontend keeps its slider; the slider just re-calls this
endpoint with the new `margin_mm`.

Deferred to M3b:
  - Caching by (file-bytes hash, margin_mm) (5x speedup on slider
    scrubbing back and forth).
  - Multi-piece detection (today we keep the largest contour only).
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Optional

from PIL import Image, ImageFilter

from .cut_path import _walk_alpha_contour
from .models import Order

logger = logging.getLogger(__name__)

# Module-level imports of rembg so unittest.mock.patch can swap
# `apps.orders.services_smart_cut.remove` / `.new_session` directly. Wrapped
# in try/except so a missing rembg install doesn't break Django boot — the
# service raises SmartCutModelUnavailable on the first real call instead.
try:
    from rembg import new_session as _rembg_new_session
    from rembg import remove
except ImportError as _rembg_exc:  # pragma: no cover — covered indirectly
    _rembg_new_session = None
    remove = None  # type: ignore[assignment]
    _rembg_import_error = _rembg_exc
else:
    _rembg_import_error = None


# === Custom exceptions ===


class NoOriginalFile(Exception):
    """The order has no `original` OrderFile yet — view returns 400."""


class SmartCutModelUnavailable(Exception):
    """rembg failed to load (ONNX missing, onnxruntime broken, OOM, etc.).
    View returns 503 so the frontend can surface a "try again" toast."""


# === Lazy session ===
#
# rembg.new_session loads the ONNX file and warms onnxruntime. First call
# can take ~5 s; subsequent calls reuse the session. Module-scoped cache
# keeps the cost amortized across all customer requests for the lifetime
# of the worker process.

_session = None


def _get_session():
    """Lazy-load the rembg session. Cached after first successful call."""
    global _session
    if _session is not None:
        return _session
    if _rembg_new_session is None:
        raise SmartCutModelUnavailable(
            f"rembg not installed: {_rembg_import_error}",
        )
    try:
        _session = _rembg_new_session("isnet-general-use")
    except Exception as exc:
        raise SmartCutModelUnavailable(
            f"rembg session init failed: {exc}",
        ) from exc
    return _session


# === Polygon helpers ===


def _drop_colinear(points: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Remove vertices that lie on the straight line between their neighbors.

    Pure post-processing pass — keeps the polygon visually identical but
    drops O(N) redundant points emitted by the per-pixel Moore walker.
    Result still has 100s of points around a typical silhouette (more than
    enough density for the frontend's quadratic-curve render). Always
    keeps ≥ 3 points; bails out early on degenerate input.
    """
    if len(points) < 3:
        return points
    out: list[tuple[int, int]] = []
    n = len(points)
    for i in range(n):
        prev = points[(i - 1) % n]
        cur = points[i]
        nxt = points[(i + 1) % n]
        # 2D cross product of (cur - prev) × (nxt - cur). Zero ⇒ colinear.
        cross = (cur[0] - prev[0]) * (nxt[1] - cur[1]) - (
            cur[1] - prev[1]
        ) * (nxt[0] - cur[0])
        if cross != 0:
            out.append(cur)
    if len(out) < 3:
        return points  # don't return a degenerate polygon — keep the dense one
    return out


def _shoelace_area(points: list[tuple[int, int]]) -> float:
    """Polygon area via the shoelace formula. Always returns absolute area."""
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


# === Public API ===


# Print-shop minimum bleed margin (millimeters). Below this, die-cutting
# tolerance alone consumes the margin and the customer's design gets
# clipped at the edge. The view clamps `margin_mm` to this floor.
MIN_MARGIN_MM = 5

# Kernel ceiling for the bleed dilation. PIL's MaxFilter scales O(kernel)
# per pixel; on a 2048×2048 image a kernel of 200 is already ~1 s. Above
# this we fall back to multiple passes with a smaller kernel — same
# Minkowski result, bounded per-pass cost.
_MAX_KERNEL = 99


def _odd(n: int) -> int:
    """PIL's MinFilter/MaxFilter take ODD kernel sizes only."""
    n = max(1, int(n))
    return n if n % 2 == 1 else n + 1


def _dilate_alpha(mask: Image.Image, total_px: int) -> Image.Image:
    """Apply MaxFilter dilation by `total_px` total pixels.

    Single-pass when total_px ≤ _MAX_KERNEL. For larger margins, repeat
    smaller-kernel passes — multi-pass dilation with kernel k1 then k2
    is equivalent to a single-pass (k1+k2-1) dilation, but each pass
    stays under the per-pixel cost ceiling. Always uses ODD kernels.
    """
    if total_px <= 0:
        return mask
    out = mask
    remaining = total_px
    # Each pass advances the boundary by (kernel - 1) // 2 pixels, so a
    # pass with kernel k extends the silhouette by (k - 1) // 2.
    while remaining > 0:
        k = _odd(min(remaining * 2 + 1, _MAX_KERNEL))
        out = out.filter(ImageFilter.MaxFilter(k))
        remaining -= (k - 1) // 2
    return out


def _px_per_mm_for_image(order: Order, image_width_px: int) -> float:
    """Convert millimeters → image-natural pixels.

    Mirrors the frontend's `pxPerMm` computation in EditorView.vue. When
    the customer has already chosen `width_mm` on /order-config, we know
    the print scale exactly; otherwise we assume the long edge prints at
    100 mm (typical sticker size). The frontend uses the same fallback,
    so the bleed margin painted on the canvas matches the dilation here.
    """
    DEFAULT_LONG_EDGE_MM = 100.0
    if order.width_mm and order.width_mm > 0:
        return image_width_px / float(order.width_mm)
    return image_width_px / DEFAULT_LONG_EDGE_MM


def smart_cut_for_order(order: Order, margin_mm: int = 15) -> dict:
    """Run AI background removal on the order's original image.

    Args:
        order: the Order (must have an `original` OrderFile).
        margin_mm: bleed margin to add around the detected silhouette.
            Clamped to MIN_MARGIN_MM = 5 (printable minimum). Default 15
            matches the frontend's slider default.

    Returns a JSON-serializable dict matching the frontend's
    SmartCutResponse type:

        {"kind": "ok",
         "points":          [...inflated cut polygon...],     # bleed-out
         "artwork_points":  [...tight artwork silhouette...], # no bleed
         "area_px": float,
         "cleaned_image_data_url": "data:image/png;base64,..."}

    or {"kind": "no-contour-found", ...} if rembg returned an empty mask.

    Raises NoOriginalFile or SmartCutModelUnavailable on the documented
    error paths; the view layer translates those to 400 / 503.
    """
    file_obj = order.files.filter(kind="original").first()
    if file_obj is None:
        raise NoOriginalFile(f"Order {order.uuid} has no 'original' file.")

    # Floor the margin at the printable minimum. Above this, no cap —
    # the slider's max is enforced on the frontend.
    margin_mm = max(MIN_MARGIN_MM, int(margin_mm))

    # rembg expects a PIL Image (or bytes). RGB strip avoids edge cases
    # where the source already has an alpha channel — we want the model
    # to redo the foreground decision from RGB alone.
    with file_obj.file.open("rb") as f:
        raw_bytes = f.read()
    pil_in = Image.open(io.BytesIO(raw_bytes)).convert("RGB")

    # Run inference. Module-cached session.
    if remove is None:
        raise SmartCutModelUnavailable(
            f"rembg not installed: {_rembg_import_error}",
        )
    session = _get_session()
    try:
        rgba = remove(pil_in, session=session)
    except Exception as exc:
        raise SmartCutModelUnavailable(
            f"rembg inference failed: {exc}",
        ) from exc
    if rgba.mode != "RGBA":
        rgba = rgba.convert("RGBA")

    # Threshold alpha → binary mask.
    alpha = rgba.split()[3].point(lambda v: 255 if v > 128 else 0)

    # === Step A: morphological opening — kill thin appendages ===
    # Without this, single-pixel-wide bridges between the silhouette and
    # decorative bits (leaves/sparkles/feathers) become huge curving
    # tendrils once we dilate. Kernel 13 on a typical 1024-px mask drops
    # the leaves but keeps fur tufts. (Tested on the gorilla repro.)
    OPEN_KERNEL = 13
    eroded = alpha.filter(ImageFilter.MinFilter(OPEN_KERNEL))
    artwork_mask = eroded.filter(ImageFilter.MaxFilter(OPEN_KERNEL))

    # Walk the TIGHT artwork silhouette first — this is the polygon the
    # customer sees as the "no-bleed" inner boundary, and what the canvas
    # uses to clip the base image when removeBackground is on.
    artwork_boundary = _walk_alpha_contour(artwork_mask)
    if artwork_boundary is None or len(artwork_boundary) < 3:
        return {
            "kind": "no-contour-found",
            "points": [],
            "artwork_points": [],
            "area_px": 0,
            "cleaned_image_data_url": None,
        }

    artwork_simplified = _drop_colinear(artwork_boundary)
    artwork_payload = [
        {"kind": "image", "x": int(x), "y": int(y)} for x, y in artwork_simplified
    ]

    # === Step B: dilate by the bleed margin → cut mask ===
    # MaxFilter performs Minkowski-sum-with-disk on the binary mask. For
    # non-convex polygons this produces a clean simple-polygon expansion
    # — exactly what the frontend's normal-bisector offset cannot do.
    px_per_mm = _px_per_mm_for_image(order, rgba.size[0])
    margin_px = int(round(margin_mm * px_per_mm))
    cut_mask = _dilate_alpha(artwork_mask, margin_px)

    cut_boundary = _walk_alpha_contour(cut_mask)
    if cut_boundary is None or len(cut_boundary) < 3:
        # Should not happen — dilation only grows the mask. If it does,
        # fall back to the tight artwork polygon so we don't break the
        # editor.
        cut_simplified = artwork_simplified
    else:
        cut_simplified = _drop_colinear(cut_boundary)

    cut_payload = [
        {"kind": "image", "x": int(x), "y": int(y)} for x, y in cut_simplified
    ]
    area_px = _shoelace_area(cut_simplified)

    # === Step C: build the visible image — RGB through the CUT mask ===
    # Pixels outside the artwork silhouette but inside the bleed ring
    # carry the ORIGINAL source RGB (whatever color was around the
    # subject in the customer's photo). That's the "background extends
    # outward" feel the customer wanted: a teal vinyl bleed for a
    # gorilla on teal, not a transparent ring of nothing.
    #
    # The rembg-cleaned RGB is what we paint with — pixels rembg
    # decided are background already have rgba alpha=0, but we use the
    # original `pil_in` so we get whatever was actually there. The cut
    # mask gates which pixels are visible.
    visible = Image.new("RGBA", pil_in.size, (0, 0, 0, 0))
    visible.paste(pil_in, mask=cut_mask)

    # Encode as a base64 PNG data URL. PNG compresses the alpha-zeroed
    # outside-the-mask area heavily; typical 1024x1024 sticker fits in
    # 50-200 KB inline.
    png_buf = io.BytesIO()
    visible.save(png_buf, format="PNG", optimize=True)
    cleaned_b64 = base64.b64encode(png_buf.getvalue()).decode("ascii")
    cleaned_data_url = f"data:image/png;base64,{cleaned_b64}"

    return {
        "kind": "ok",
        "points": cut_payload,
        "artwork_points": artwork_payload,
        "area_px": area_px,
        "cleaned_image_data_url": cleaned_data_url,
    }


__all__ = (
    "smart_cut_for_order",
    "NoOriginalFile",
    "SmartCutModelUnavailable",
    "MIN_MARGIN_MM",
)
