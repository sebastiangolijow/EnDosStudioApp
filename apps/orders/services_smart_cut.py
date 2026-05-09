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
  4. Walk the boundary with apps.orders.cut_path._walk_alpha_contour
     (the same Moore tracer used by SVG cut-path generation).
  5. Drop colinear runs (cheap O(n) post-pass — keeps ≥ 3 pts).
  6. Return JSON-serializable dict matching the frontend's expected
     polygon shape (image-natural-pixel coordinates, no bleed offset —
     the frontend handles bleed margin via its existing slider).

Why no bleed offset on the backend: the frontend already owns
`offsetPolygonOutward` in src/workers/autoCrop.worker.ts plus the
`marginMm` slider + `pxPerMm` derivation logic. Replicating that
server-side would split the source of truth across two languages. The
smart-cut polygon is symmetric to the worker's `artworkPoints` (tight
artwork silhouette, no margin); customers who want margin re-run the
classical Auto cut with the slider.

Deferred to M3b:
  - Caching by file-bytes hash (5x speedup on customer re-clicks).
  - Multi-piece detection (today we keep the largest contour only).
  - Backend bleed-margin offset (mirror offsetPolygonOutward in Python
    so smart cut + slider play together natively).
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


def smart_cut_for_order(order: Order) -> dict:
    """Run AI background removal on the order's original image.

    Returns a JSON-serializable dict matching the frontend's
    SmartCutResponse type:

        {"kind": "ok", "points": [{"kind": "image", "x": ..., "y": ...}, ...],
         "artwork_points": [...same array...], "area_px": float}

    or {"kind": "no-contour-found", "points": [], "artwork_points": [],
       "area_px": 0} if rembg returned a fully transparent / empty mask.

    Raises NoOriginalFile or SmartCutModelUnavailable on the documented
    error paths; the view layer translates those to 400 / 503.
    """
    file_obj = order.files.filter(kind="original").first()
    if file_obj is None:
        raise NoOriginalFile(f"Order {order.uuid} has no 'original' file.")

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

    # Morphological opening: erode then dilate. Drops thin appendages
    # (single-pixel-wide bridges between the main silhouette and
    # decorative bits like leaves/sparkles/feathers in the source art),
    # collapses tiny disconnected islands, and smooths jagged single-
    # pixel boundary noise. Without this, those thin bridges become
    # huge curving "tendrils" when the frontend offsets the polygon
    # outward by the bleed margin.
    #
    # Kernel size = 13 px on a typical 1024-px mask gets the leaves out
    # but keeps fur tufts (which are wider than 13 px). PIL's MinFilter
    # / MaxFilter take ODD kernel sizes only.
    kernel = 13
    eroded = alpha.filter(ImageFilter.MinFilter(kernel))
    cleaned = eroded.filter(ImageFilter.MaxFilter(kernel))

    boundary = _walk_alpha_contour(cleaned)
    if boundary is None or len(boundary) < 3:
        return {
            "kind": "no-contour-found",
            "points": [],
            "artwork_points": [],
            "area_px": 0,
            "cleaned_image_data_url": None,
        }

    simplified = _drop_colinear(boundary)
    area_px = _shoelace_area(simplified)
    points_payload = [
        {"kind": "image", "x": int(x), "y": int(y)} for x, y in simplified
    ]

    # Build the visible image: rembg's RGB + the OPENED alpha mask.
    # Critical: the alpha channel here must match the polygon contour
    # we just traced. If we used the raw rembg alpha, decorative bits
    # the morphological opening dropped (leaves, sparkles) would still
    # be visible to the customer — but outside the cut polygon, so
    # they'd be visually clipped off. Better to drop them at both
    # layers consistently.
    rgb_only = rgba.convert("RGB")
    visible = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    visible.paste(rgb_only, mask=cleaned)

    # Encode as a base64 PNG data URL so the frontend can swap it in as
    # the canvas's base layer. PNG compresses alpha-zeroed pixels to
    # almost nothing — for a typical 1024x1024 sticker the data URL is
    # 50-200 KB. Inline transmission is fine; no point setting up a
    # separate file endpoint just for this.
    png_buf = io.BytesIO()
    visible.save(png_buf, format="PNG", optimize=True)
    cleaned_b64 = base64.b64encode(png_buf.getvalue()).decode("ascii")
    cleaned_data_url = f"data:image/png;base64,{cleaned_b64}"

    # `artwork_points` is identical to `points` in this version (no
    # backend bleed offset). Keeping the field present means M3b can
    # introduce real backend offset without a wire-format change.
    return {
        "kind": "ok",
        "points": points_payload,
        "artwork_points": points_payload,
        "area_px": area_px,
        "cleaned_image_data_url": cleaned_data_url,
    }


__all__ = (
    "smart_cut_for_order",
    "NoOriginalFile",
    "SmartCutModelUnavailable",
)
