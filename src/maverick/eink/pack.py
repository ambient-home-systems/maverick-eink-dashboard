"""Pack palette indices into the byte layouts panel controllers expect.

Three families cover essentially every panel Maverick targets:

``packed``
    N bits per pixel, pixels left-to-right, rows top-to-bottom. Used by most
    greyscale controllers (IT8951 at 4bpp) and by index-addressed colour panels
    such as Spectra 6 and ACeP.
``planes``
    One 1-bit plane per ink, concatenated. This is the classic Waveshare
    black/white + red layout, where two full-size buffers are clocked out in
    sequence.
``indexed``
    One byte per pixel. Wasteful on the wire but the simplest thing for a
    device or transport that does its own conversion.

Transports that hand a *PIL image* to a library (OpenDisplay, Inky) never come
through here — the library owns the wire format. This module is for the
transports that push raw frames.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from .palette import Palette


class FrameFormat(str, Enum):
    PACKED = "packed"
    PLANES = "planes"
    INDEXED = "indexed"
    PNG = "png"
    BMP = "bmp"


@dataclass(frozen=True)
class PackOptions:
    """Controller quirks that vary between otherwise identical panels."""

    msb_first: bool = True
    invert: bool = False
    #: Ink order for ``planes``. Defaults to black first, then spot colours.
    plane_order: tuple[str, ...] | None = None
    #: Some controllers clock 1 for the *absence* of an ink on the black plane.
    plane_active_low: bool = False


def bits_per_pixel(palette: Palette) -> int:
    return palette.bits_per_pixel


def pack_packed(
    indices: np.ndarray,
    palette: Palette,
    options: PackOptions = PackOptions(),
) -> bytes:
    """Pack indices at ``palette.bits_per_pixel``, rows padded to byte boundaries."""
    bpp = palette.bits_per_pixel
    if bpp not in (1, 2, 4, 8):
        raise ValueError(f"unsupported bit depth {bpp}")

    values = indices.astype(np.uint8)
    if options.invert:
        values = np.uint8(len(palette) - 1) - values

    if bpp == 8:
        return values.tobytes()

    h, w = values.shape
    per_byte = 8 // bpp
    pad = (-w) % per_byte
    if pad:
        # Pad with white so a partial final byte reads as background, not a
        # black bar down the edge of the panel.
        values = np.pad(values, ((0, 0), (0, pad)), constant_values=palette.white_index)

    grouped = values.reshape(h, -1, per_byte).astype(np.uint16)
    shifts = (
        np.arange(per_byte - 1, -1, -1) * bpp
        if options.msb_first
        else np.arange(per_byte) * bpp
    )
    mask = (1 << bpp) - 1
    packed = ((grouped & mask) << shifts).sum(axis=2).astype(np.uint8)
    return packed.tobytes()


def pack_planes(
    indices: np.ndarray,
    palette: Palette,
    options: PackOptions = PackOptions(),
) -> bytes:
    """Pack one 1-bit plane per ink, concatenated in ``plane_order``.

    The black plane marks pixels that are black; each spot-colour plane marks
    pixels of that ink. White is the absence of every plane.
    """
    order = options.plane_order
    if order is None:
        order = tuple(n for n in palette.names if n != "white")
    h, w = indices.shape
    pad = (-w) % 8
    out = bytearray()

    for ink in order:
        idx = palette.index_of(ink)
        plane = indices == idx
        if options.plane_active_low:
            plane = ~plane
        if pad:
            plane = np.pad(plane, ((0, 0), (0, pad)), constant_values=options.plane_active_low)
        bits = plane.reshape(h, -1, 8)
        shifts = (
            np.arange(7, -1, -1) if options.msb_first else np.arange(8)
        ).astype(np.uint8)
        out += ((bits.astype(np.uint8)) << shifts).sum(axis=2).astype(np.uint8).tobytes()

    return bytes(out)


def pack(
    indices: np.ndarray,
    palette: Palette,
    fmt: FrameFormat | str = FrameFormat.PACKED,
    options: PackOptions = PackOptions(),
) -> bytes:
    """Pack an index array into ``fmt``."""
    fmt = FrameFormat(fmt)
    if fmt is FrameFormat.PACKED:
        return pack_packed(indices, palette, options)
    if fmt is FrameFormat.PLANES:
        return pack_planes(indices, palette, options)
    if fmt is FrameFormat.INDEXED:
        return indices.astype(np.uint8).tobytes()
    raise ValueError(f"{fmt.value} is an image format, not a raw frame format")


def expected_size(width: int, height: int, palette: Palette, fmt: FrameFormat | str) -> int:
    """Byte length ``pack`` will produce — useful for validating device buffers."""
    fmt = FrameFormat(fmt)
    if fmt is FrameFormat.INDEXED:
        return width * height
    if fmt is FrameFormat.PLANES:
        return ((width + 7) // 8) * height * (len(palette) - 1)
    bpp = palette.bits_per_pixel
    per_byte = 8 // bpp
    return ((width + per_byte - 1) // per_byte) * height


__all__ = ["FrameFormat", "PackOptions", "pack", "pack_packed", "pack_planes", "expected_size"]
