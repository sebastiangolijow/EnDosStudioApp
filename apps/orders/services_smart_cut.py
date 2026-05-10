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
import time
from typing import Optional

import numpy as np
from PIL import Image
from scipy import ndimage

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

# Long-edge target for the mask-processing pipeline. rembg runs at full
# resolution (model-determined), but morph-opening, bleed dilation, and
# contour walking all run on a downsampled binary mask — a silhouette
# at 512 px is visually indistinguishable from one at 1024 px once the
# polygon is rendered, and the downstream ops are 4-16× faster.
_MASK_PROCESSING_LONG_EDGE = 512

# Pixel kernel for the morph-opening pass that drops thin bridges from
# rembg output. Sized at the DOWNSAMPLED resolution — at 512 px long
# edge, kernel 7 px ≈ kernel 13-14 on the original. (Tested on the
# gorilla repro: drops the leaves, keeps fur tufts.)
_OPEN_KERNEL_DOWNSAMPLED = 7

# Smoothness slider: maps the customer-facing 1-10 value to a Gaussian
# sigma (in DOWNSAMPLED pixels) applied to the binary mask before the
# contour walker traces it. Higher sigma = rounder cuts. The defaults
# below were tuned against the gorilla illustration so default smoothness
# (5) produces a cut line a vinyl plotter can physically follow without
# stalling on every fur-tuft notch.
#
# Why smooth the mask, not the polygon: blurring a binary mask is a true
# 2-D morphological smoothing — narrow concavities get filled, wide ones
# survive. Smoothing the polygon (perimeter-Gaussian on vertices) cannot
# do that — it just averages adjacent points and a deep narrow notch
# survives as a pinched feature.
_SMOOTH_SIGMA_MIN = 1.0
_SMOOTH_SIGMA_MAX = 8.0
_SMOOTH_DEFAULT = 5  # 1-10 scale; 5 ≈ sigma 4 px

# Threshold applied to the blurred float mask to rebinarize. 0.5 keeps
# the cut polygon's signed-distance to the original silhouette near
# zero; lower values would expand the polygon, higher would shrink.
_SMOOTH_THRESHOLD = 0.5


def _binary_dilate(mask_array: np.ndarray, radius_px: int) -> np.ndarray:
    """Dilate a binary 2-D bool/uint8 array by `radius_px` pixels.

    scipy's `binary_dilation` is C-implemented and orders of magnitude
    faster than PIL's MaxFilter on big kernels. With `iterations=N`
    using the default 3×3 cross structuring element, every pass extends
    the silhouette by 1 px, so we set iterations directly to the radius.

    Returns a bool array same shape as input.
    """
    if radius_px <= 0:
        return mask_array.astype(bool, copy=True)
    return ndimage.binary_dilation(mask_array, iterations=int(radius_px))


def _binary_open(mask_array: np.ndarray, kernel_size: int) -> np.ndarray:
    """Morphological opening (erode then dilate) by `kernel_size` pixels.

    Drops thin appendages narrower than `kernel_size`. Same scipy path,
    same speed advantage. Translates `kernel_size` (odd, like a filter
    window) to iterations = kernel_size // 2 for the cross structuring
    element.
    """
    iters = max(1, kernel_size // 2)
    eroded = ndimage.binary_erosion(mask_array, iterations=iters)
    return ndimage.binary_dilation(eroded, iterations=iters)


def _smooth_mask(mask_array: np.ndarray, sigma_px: float) -> np.ndarray:
    """Gaussian-smooth a binary mask, then re-threshold.

    The math: convert to float, run scipy.ndimage.gaussian_filter, threshold
    at 0.5. A pixel survives the threshold if more than half of the
    Gaussian-weighted neighborhood was foreground — equivalent to rounding
    the boundary at radius ≈ sigma_px.

    Critical for cuttability: the rembg silhouette of an illustration with
    fur / hair / fine details has many concavities narrower than any
    physical cutter blade can navigate. Blurring the mask fills those
    notches at the source, producing a polygon a vinyl plotter can actually
    follow without stalling.

    sigma_px=0 short-circuits to a copy (no work). All ops at downsampled
    resolution, so sigma values are tiny — 8 px on a 512-px-edge mask is
    ~16 px on the original, plenty of rounding without losing the overall
    silhouette shape.
    """
    if sigma_px <= 0:
        return mask_array.astype(bool, copy=True)
    float_mask = mask_array.astype(np.float32)
    blurred = ndimage.gaussian_filter(float_mask, sigma=sigma_px)
    return blurred > _SMOOTH_THRESHOLD


def _sigma_for_smoothness(smoothness_1_to_10: int) -> float:
    """Map the customer's 1-10 smoothness slider to a Gaussian sigma in
    downsampled pixels. Linear interpolation between MIN and MAX."""
    s = max(1, min(10, int(smoothness_1_to_10)))
    return _SMOOTH_SIGMA_MIN + (s - 1) * (_SMOOTH_SIGMA_MAX - _SMOOTH_SIGMA_MIN) / 9.0


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


def smart_cut_for_order(
    order: Order, margin_mm: int = 15, smoothness: int = _SMOOTH_DEFAULT
) -> dict:
    """Run AI background removal on the order's original image.

    Args:
        order: the Order (must have an `original` OrderFile).
        margin_mm: bleed margin to add around the detected silhouette.
            Clamped to MIN_MARGIN_MM = 5 (printable minimum). Default 15
            matches the frontend's slider default.
        smoothness: 1-10 slider value controlling how aggressively the
            cut line rounds sharp concavities (fur, hair, decoration
            spikes). Default 5 produces a cut line a typical vinyl
            plotter can physically follow. 1 = follow silhouette tightly
            (may be uncuttable on detailed art); 10 = very rounded.

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

    # Per-step timing for performance debugging. Logged at INFO so a
    # single grep in docker logs reveals the breakdown. The numbers
    # informed the downsampling + scipy switch — keep them around so we
    # catch regressions if a future change makes a step slow again.
    timings: dict[str, float] = {}
    t0 = time.perf_counter()

    # rembg expects a PIL Image (or bytes). RGB strip avoids edge cases
    # where the source already has an alpha channel — we want the model
    # to redo the foreground decision from RGB alone.
    with file_obj.file.open("rb") as f:
        raw_bytes = f.read()
    pil_in = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    timings["read_decode"] = time.perf_counter() - t0

    # Run inference. Module-cached session (warmed at boot — see apps.py).
    if remove is None:
        raise SmartCutModelUnavailable(
            f"rembg not installed: {_rembg_import_error}",
        )
    session = _get_session()
    t1 = time.perf_counter()
    try:
        rgba = remove(pil_in, session=session)
    except Exception as exc:
        raise SmartCutModelUnavailable(
            f"rembg inference failed: {exc}",
        ) from exc
    if rgba.mode != "RGBA":
        rgba = rgba.convert("RGBA")
    timings["rembg"] = time.perf_counter() - t1

    # === Downsample for mask processing ===
    # Walking a 1024×1024 contour in pure Python is slow (~0.5-1 s per
    # walk, and we do it twice). Scipy's morph ops are fast but still
    # cheaper at 512² than 1024². The polygon visible to the customer
    # at canvas-render scale doesn't need the extra precision — the
    # cut mask uploaded to the printer is regenerated from the polygon
    # at print resolution server-side anyway.
    natural_w, natural_h = rgba.size
    long_edge = max(natural_w, natural_h)
    if long_edge > _MASK_PROCESSING_LONG_EDGE:
        scale = _MASK_PROCESSING_LONG_EDGE / long_edge
        proc_w = int(round(natural_w * scale))
        proc_h = int(round(natural_h * scale))
    else:
        scale = 1.0
        proc_w, proc_h = natural_w, natural_h

    t2 = time.perf_counter()
    # Downsample alpha → binary numpy array. NEAREST keeps the binary
    # threshold crisp; bilinear would muddy the edge.
    alpha_full = rgba.split()[3]
    if scale < 1.0:
        alpha_small = alpha_full.resize((proc_w, proc_h), Image.NEAREST)
    else:
        alpha_small = alpha_full
    mask_arr = np.asarray(alpha_small, dtype=np.uint8) > 128
    timings["downsample"] = time.perf_counter() - t2

    # === Step A: morphological opening (scipy, downsampled) ===
    t3 = time.perf_counter()
    artwork_arr = _binary_open(mask_arr, _OPEN_KERNEL_DOWNSAMPLED)
    timings["morph_open"] = time.perf_counter() - t3

    if not artwork_arr.any():
        return {
            "kind": "no-contour-found",
            "points": [],
            "artwork_points": [],
            "area_px": 0,
            "cleaned_image_data_url": None,
        }

    # === Step B: bleed-margin dilation (scipy) ===
    # margin_mm → natural-resolution px → downsampled px so the dilation
    # iterations match the customer's intent in physical millimeters.
    px_per_mm_natural = _px_per_mm_for_image(order, natural_w)
    margin_px_natural = int(round(margin_mm * px_per_mm_natural))
    margin_px_proc = max(1, int(round(margin_px_natural * scale)))

    t4 = time.perf_counter()
    cut_arr = _binary_dilate(artwork_arr, margin_px_proc)
    timings["dilate"] = time.perf_counter() - t4

    # === Step B': Gaussian-smooth both masks for cuttable boundaries ===
    # The contour walker emits one vertex per boundary pixel — on a
    # detailed silhouette (fur, hair, decoration) that's hundreds of
    # near-zero-radius concavities a vinyl plotter physically can't
    # follow. Blurring the binary mask before the walk rounds those
    # notches at the source. Apply to BOTH masks with the same sigma
    # so the artwork-clip and cut-line stay parallel curves and the
    # bleed ring keeps a uniform width.
    sigma_px = _sigma_for_smoothness(smoothness)
    t_smooth = time.perf_counter()
    artwork_arr = _smooth_mask(artwork_arr, sigma_px)
    cut_arr = _smooth_mask(cut_arr, sigma_px)
    timings["smooth"] = time.perf_counter() - t_smooth

    if not artwork_arr.any():
        # Smoothing erased the silhouette (would only happen if the
        # silhouette was a thin line and sigma was huge). Bail.
        return {
            "kind": "no-contour-found",
            "points": [],
            "artwork_points": [],
            "area_px": 0,
            "cleaned_image_data_url": None,
        }

    # === Step C: walk both contours (downsampled, then scale up) ===
    t5 = time.perf_counter()
    artwork_pil_small = Image.fromarray(
        (artwork_arr.astype(np.uint8)) * 255, mode="L"
    )
    cut_pil_small = Image.fromarray(
        (cut_arr.astype(np.uint8)) * 255, mode="L"
    )
    artwork_boundary = _walk_alpha_contour(artwork_pil_small)
    cut_boundary = _walk_alpha_contour(cut_pil_small)
    timings["contour_walk"] = time.perf_counter() - t5

    if artwork_boundary is None or len(artwork_boundary) < 3:
        return {
            "kind": "no-contour-found",
            "points": [],
            "artwork_points": [],
            "area_px": 0,
            "cleaned_image_data_url": None,
        }

    artwork_simplified = _drop_colinear(artwork_boundary)
    cut_simplified = (
        _drop_colinear(cut_boundary)
        if cut_boundary and len(cut_boundary) >= 3
        else artwork_simplified  # dilation can only grow → very rare
    )

    # Scale polygon coords back to natural-resolution pixels.
    inv_scale = 1.0 / scale if scale > 0 else 1.0
    artwork_payload = [
        {"kind": "image", "x": int(round(x * inv_scale)), "y": int(round(y * inv_scale))}
        for x, y in artwork_simplified
    ]
    cut_payload = [
        {"kind": "image", "x": int(round(x * inv_scale)), "y": int(round(y * inv_scale))}
        for x, y in cut_simplified
    ]
    area_px = _shoelace_area(cut_simplified) * (inv_scale * inv_scale)

    # === Step D: build the visible image — RGB through the CUT mask ===
    # Upsample the cut mask back to natural resolution so the paste
    # gates the original full-res RGB pixels (no quality loss in the
    # cleaned PNG the customer sees).
    t6 = time.perf_counter()
    if scale < 1.0:
        cut_mask_full = Image.fromarray(
            (cut_arr.astype(np.uint8)) * 255, mode="L"
        ).resize((natural_w, natural_h), Image.NEAREST)
    else:
        cut_mask_full = cut_pil_small
    visible = Image.new("RGBA", pil_in.size, (0, 0, 0, 0))
    visible.paste(pil_in, mask=cut_mask_full)
    timings["compose_visible"] = time.perf_counter() - t6

    t7 = time.perf_counter()
    png_buf = io.BytesIO()
    visible.save(png_buf, format="PNG", optimize=True)
    cleaned_b64 = base64.b64encode(png_buf.getvalue()).decode("ascii")
    cleaned_data_url = f"data:image/png;base64,{cleaned_b64}"
    timings["png_encode"] = time.perf_counter() - t7

    timings["total"] = time.perf_counter() - t0
    logger.info(
        "smart_cut order=%s margin_mm=%s smoothness=%s natural=%sx%s proc=%sx%s timings=%s",
        order.uuid,
        margin_mm,
        smoothness,
        natural_w,
        natural_h,
        proc_w,
        proc_h,
        {k: round(v, 3) for k, v in timings.items()},
    )

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
