"""Palette quantisation and dithering for e-ink output.

Why this is not just ``Image.quantize``
---------------------------------------
A Home Assistant dashboard is mostly flat fills, rules and text, with the
occasional weather icon, graph gradient or camera thumbnail. Error diffusion is
the right answer for the second group and actively harmful for the first: it
sprays isolated pixels through solid backgrounds and eats the thin stems of
glyphs, which on a 120 dpi panel is the difference between readable and not.

``DitherMode.AUTO`` (the default for dashboards) therefore diffuses error only
where the source image is locally busy, and snaps flat regions straight to the
nearest ink. Photographs still dither; your temperature readout stays crisp.
"""

from __future__ import annotations

from enum import Enum

import numpy as np
from PIL import Image

from .palette import Palette

# Error-diffusion kernels as (dx, dy, weight); weights are divided by `divisor`.
_KERNELS: dict[str, tuple[int, tuple[tuple[int, int, int], ...]]] = {
    "floyd_steinberg": (16, ((1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1))),
    "atkinson": (8, ((1, 0, 1), (2, 0, 1), (-1, 1, 1), (0, 1, 1), (1, 1, 1), (0, 2, 1))),
    "burkes": (32, ((1, 0, 8), (2, 0, 4), (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2))),
    "sierra_lite": (4, ((1, 0, 2), (-1, 1, 1), (0, 1, 1))),
    "sierra": (
        32,
        (
            (1, 0, 5), (2, 0, 3),
            (-2, 1, 2), (-1, 1, 4), (0, 1, 5), (1, 1, 4), (2, 1, 2),
            (-1, 2, 2), (0, 2, 3), (1, 2, 2),
        ),
    ),
    "stucki": (
        42,
        (
            (1, 0, 8), (2, 0, 4),
            (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2),
            (-2, 2, 1), (-1, 2, 2), (0, 2, 4), (1, 2, 2), (2, 2, 1),
        ),
    ),
    "jarvis": (
        48,
        (
            (1, 0, 7), (2, 0, 5),
            (-2, 1, 3), (-1, 1, 5), (0, 1, 7), (1, 1, 5), (2, 1, 3),
            (-2, 2, 1), (-1, 2, 3), (0, 2, 5), (1, 2, 3), (2, 2, 1),
        ),
    ),
}


class DitherMode(str, Enum):
    """How to map continuous-tone pixels onto the panel's inks."""

    AUTO = "auto"
    NONE = "none"
    ORDERED = "ordered"
    FLOYD_STEINBERG = "floyd_steinberg"
    ATKINSON = "atkinson"
    BURKES = "burkes"
    SIERRA = "sierra"
    SIERRA_LITE = "sierra_lite"
    STUCKI = "stucki"
    JARVIS = "jarvis"


_BAYER8 = (
    np.array(
        [
            [0, 32, 8, 40, 2, 34, 10, 42],
            [48, 16, 56, 24, 50, 18, 58, 26],
            [12, 44, 4, 36, 14, 46, 6, 38],
            [60, 28, 52, 20, 62, 30, 54, 22],
            [3, 35, 11, 43, 1, 33, 9, 41],
            [51, 19, 59, 27, 49, 17, 57, 25],
            [15, 47, 7, 39, 13, 45, 5, 37],
            [63, 31, 55, 23, 61, 29, 53, 21],
        ],
        dtype=np.float32,
    )
    / 64.0
) - 0.5


def _palette_array(palette: Palette) -> np.ndarray:
    return np.asarray(palette.colors, dtype=np.float32)


def _nearest_indices(rgb: np.ndarray, pal: np.ndarray) -> np.ndarray:
    """Nearest palette index per pixel, in perceptually weighted RGB space.

    Plain Euclidean RGB distance sends mid greys to a panel's red ink often
    enough to matter, so channels are weighted roughly by luminance response.
    """
    weights = np.array([0.299, 0.587, 0.114], dtype=np.float32)
    h, w, _ = rgb.shape
    flat = rgb.reshape(-1, 1, 3)
    diff = (flat - pal.reshape(1, -1, 3)) * weights
    dist = np.einsum("ijk,ijk->ij", diff, diff)
    return dist.argmin(axis=1).astype(np.uint8).reshape(h, w)


def _build_nearest_lut(pal: np.ndarray) -> np.ndarray:
    """A 32x32x32 nearest-ink lookup table for the error-diffusion inner loop.

    Quantising each channel to 5 bits costs at most 4 levels of placement error,
    which error diffusion absorbs completely, and turns a per-pixel distance
    computation over the whole palette into a single array read. This is what
    makes the non-Pillow kernels usable on a 1872x1404 panel.
    """
    grid = (np.arange(32, dtype=np.float32) * (255.0 / 31.0)).astype(np.float32)
    r, g, b = np.meshgrid(grid, grid, grid, indexing="ij")
    cube = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    weights = np.array([0.299, 0.587, 0.114], dtype=np.float32)
    diff = (cube[:, None, :] - pal[None, :, :]) * weights
    idx = np.einsum("ijk,ijk->ij", diff, diff).argmin(axis=1)
    return idx.astype(np.uint8).reshape(32, 32, 32)


def _diffuse(rgb: np.ndarray, pal: np.ndarray, kernel: str, serpentine: bool) -> np.ndarray:
    """Error diffusion with an arbitrary kernel.

    Row-serial by nature, so the hot loop runs on flat Python lists (faster than
    numpy scalar indexing) against a precomputed nearest-ink LUT. Propagated
    error is clamped, which stops the dark halos naive implementations smear off
    high-contrast UI edges.
    """
    divisor, taps = _KERNELS[kernel]
    lut = _build_nearest_lut(pal)
    h, w, _ = rgb.shape
    inv = 1.0 / divisor
    pal_list = [(float(c[0]), float(c[1]), float(c[2])) for c in pal]

    # Flat per-channel buffers; index = y * w + x.
    flat = rgb.astype(np.float32).reshape(-1, 3)
    br = flat[:, 0].tolist()
    bg = flat[:, 1].tolist()
    bb = flat[:, 2].tolist()
    out = [0] * (h * w)
    weighted_taps = [(dx, dy, weight * inv) for dx, dy, weight in taps]

    for y in range(h):
        row = y * w
        reverse = serpentine and (y % 2)
        xs = range(w - 1, -1, -1) if reverse else range(w)
        direction = -1 if reverse else 1
        for x in xs:
            i = row + x
            orr, og, ob = br[i], bg[i], bb[i]
            qr = 0 if orr < 0.0 else (31 if orr > 255.0 else int(orr * 0.12156862745))
            qg = 0 if og < 0.0 else (31 if og > 255.0 else int(og * 0.12156862745))
            qb = 0 if ob < 0.0 else (31 if ob > 255.0 else int(ob * 0.12156862745))
            idx = lut[qr, qg, qb]
            out[i] = idx
            pr, pg, pb = pal_list[idx]
            er, eg, eb = orr - pr, og - pg, ob - pb
            if er < -160.0:
                er = -160.0
            elif er > 160.0:
                er = 160.0
            if eg < -160.0:
                eg = -160.0
            elif eg > 160.0:
                eg = 160.0
            if eb < -160.0:
                eb = -160.0
            elif eb > 160.0:
                eb = 160.0
            for dx, dy, weight in weighted_taps:
                nx = x + dx * direction
                if nx < 0 or nx >= w:
                    continue
                ny = y + dy
                if ny >= h:
                    continue
                j = ny * w + nx
                br[j] += er * weight
                bg[j] += eg * weight
                bb[j] += eb * weight

    return np.array(out, dtype=np.uint8).reshape(h, w)


def _ordered(rgb: np.ndarray, pal: np.ndarray) -> np.ndarray:
    """Bayer-matrix dithering: no error propagation, so text edges survive."""
    h, w, _ = rgb.shape
    tile = np.tile(_BAYER8, (h // 8 + 1, w // 8 + 1))[:h, :w]
    # Spread is the mean nearest-neighbour distance between inks; it controls how
    # much threshold noise a palette can absorb before banding turns into mush.
    if len(pal) > 1:
        spread = float(np.mean(np.sort(np.linalg.norm(pal[:, None] - pal[None, :], axis=2))[:, 1]))
    else:
        spread = 0.0
    noise = (tile * spread)[:, :, None]
    return _nearest_indices(np.clip(rgb + noise, 0, 255), pal)


def _continuous_tone_mask(
    rgb: np.ndarray,
    low: int = 26,
    high: int = 229,
    erode_radius: int = 2,
    dilate_radius: int = 2,
) -> np.ndarray:
    """Mark regions that genuinely need error diffusion.

    The naive approach — "dither wherever there is local contrast" — is wrong,
    because *text* has more local contrast than anything else on a dashboard.
    Diffusing across a glyph erodes its stems and blotches its bowls, which is
    visibly worse than simply thresholding it.

    What actually distinguishes a photograph or a gradient is a *broad field of
    intermediate tone*. Text is bimodal: near-black glyph, near-white ground,
    with intermediate values confined to a one-or-two-pixel antialiasing fringe.
    So: select midtone pixels, erode away anything thinner than the fringe, then
    dilate back. Photographs survive; glyph edges do not.
    """
    lum = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    midtone = (lum > low) & (lum < high)
    if not midtone.any():
        return midtone
    return _dilate(_erode(midtone, erode_radius), dilate_radius)


def _shift_stack(mask: np.ndarray, radius: int) -> np.ndarray:
    pad = np.pad(mask, radius, mode="constant", constant_values=False)
    h, w = mask.shape
    return np.stack(
        [
            pad[dy : dy + h, dx : dx + w]
            for dy in range(2 * radius + 1)
            for dx in range(2 * radius + 1)
        ],
        axis=0,
    )


def _erode(mask: np.ndarray, radius: int) -> np.ndarray:
    return _shift_stack(mask, radius).all(axis=0)


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    return _shift_stack(mask, radius).any(axis=0)


def quantize(
    image: Image.Image,
    palette: Palette,
    mode: DitherMode | str = DitherMode.AUTO,
    serpentine: bool = True,
) -> np.ndarray:
    """Quantise an RGB image to palette indices.

    Returns a ``(h, w)`` uint8 array of indices into ``palette.colors``.
    """
    mode = DitherMode(mode)
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    pal = _palette_array(palette)

    if mode is DitherMode.NONE:
        return _nearest_indices(rgb, pal)
    if mode is DitherMode.ORDERED:
        return _ordered(rgb, pal)
    if mode is DitherMode.AUTO:
        flat = _nearest_indices(rgb, pal)
        mask = _continuous_tone_mask(rgb)
        if not mask.any():
            return flat
        diffused = _diffuse_fast(rgb, palette, serpentine)
        return np.where(mask, diffused, flat)
    return _diffuse(rgb, pal, mode.value, serpentine)


def _diffuse_fast(rgb: np.ndarray, palette: Palette, serpentine: bool) -> np.ndarray:
    """Floyd-Steinberg via Pillow's C implementation.

    Pillow only offers Floyd-Steinberg, but it is ~100x faster than the Python
    loop and it is the kernel AUTO wants anyway.
    """
    del serpentine  # Pillow's implementation is always left-to-right.
    src = Image.fromarray(rgb.astype(np.uint8), mode="RGB")
    ref = Image.new("P", (1, 1))
    flat = palette.flat()
    ref.putpalette(flat + [0] * (768 - len(flat)))
    quantised = src.quantize(palette=ref, dither=Image.Dither.FLOYDSTEINBERG)
    idx = np.asarray(quantised, dtype=np.uint8)
    # Pillow may emit indices beyond the real palette if it pads; clamp them.
    return np.clip(idx, 0, len(palette) - 1)


def indices_to_image(indices: np.ndarray, palette: Palette) -> Image.Image:
    """Render palette indices back to a viewable RGB preview."""
    pal = np.asarray(palette.colors, dtype=np.uint8)
    return Image.fromarray(pal[np.clip(indices, 0, len(palette) - 1)], mode="RGB")


__all__ = ["DitherMode", "quantize", "indices_to_image"]
