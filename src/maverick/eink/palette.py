"""Colour schemes and palettes for e-ink panels.

E-ink pigments are nothing like sRGB primaries: the "red" of a BWR panel is a
dull brick, and Spectra 6 green is closer to olive. Quantising a dashboard
against idealised primaries produces washed-out, muddy output, so every scheme
here carries *measured* approximations of what the panel actually emits.

Measured values are approximate and panel-batch dependent. Any display may
override them in configuration via ``palette_overrides``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

RGB = tuple[int, int, int]


class ColorScheme(str, Enum):
    """The color capability of a panel."""

    MONO = "mono"
    BWR = "bwr"
    BWY = "bwy"
    BWRY = "bwry"
    GRAY4 = "gray4"
    GRAY8 = "gray8"
    GRAY16 = "gray16"
    SPECTRA6 = "spectra6"
    ACEP7 = "acep7"

    @property
    def is_greyscale(self) -> bool:
        return self in {
            ColorScheme.MONO,
            ColorScheme.GRAY4,
            ColorScheme.GRAY8,
            ColorScheme.GRAY16,
        }

    @property
    def is_colour(self) -> bool:
        return not self.is_greyscale

    @property
    def has_spot_colour(self) -> bool:
        """True for panels that add one or two accent inks to black/white."""
        return self in {ColorScheme.BWR, ColorScheme.BWY, ColorScheme.BWRY}


# --------------------------------------------------------------------------- #
# Measured ink values
# --------------------------------------------------------------------------- #

# A "white" e-ink background is never 255 — it is a light warm grey, because the
# particles scatter rather than emit. Quantising against true white makes every
# render look over-exposed on the panel.
INK_WHITE: RGB = (233, 231, 224)
INK_BLACK: RGB = (26, 26, 26)
INK_RED: RGB = (156, 44, 40)
INK_YELLOW: RGB = (206, 186, 70)
INK_BLUE: RGB = (48, 66, 129)
INK_GREEN: RGB = (62, 110, 72)
INK_ORANGE: RGB = (192, 104, 46)


def _grey_ramp(levels: int) -> tuple[RGB, ...]:
    """Evenly spaced greys spanning the panel's real black and white points."""
    if levels < 2:
        raise ValueError("levels must be >= 2")
    ramp: list[RGB] = []
    for i in range(levels):
        t = i / (levels - 1)
        ramp.append(
            (
                round(INK_BLACK[0] + (INK_WHITE[0] - INK_BLACK[0]) * t),
                round(INK_BLACK[1] + (INK_WHITE[1] - INK_BLACK[1]) * t),
                round(INK_BLACK[2] + (INK_WHITE[2] - INK_BLACK[2]) * t),
            )
        )
    return tuple(ramp)


@dataclass(frozen=True)
class Palette:
    """An ordered list of the colours a panel can actually produce.

    Index order is significant: it is the value written into packed frame
    buffers, so it must match the panel controller's colour codes.
    """

    scheme: ColorScheme
    colors: tuple[RGB, ...]
    names: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.colors) != len(self.names):
            raise ValueError("colors and names must be the same length")
        if len(self.colors) < 2:
            raise ValueError("a palette needs at least two colours")

    def __len__(self) -> int:
        return len(self.colors)

    @property
    def bits_per_pixel(self) -> int:
        """Bits needed to store one pixel index, rounded to a packable width."""
        n = len(self.colors)
        if n <= 2:
            return 1
        if n <= 4:
            return 2
        return 4

    @property
    def white_index(self) -> int:
        return self.names.index("white")

    @property
    def black_index(self) -> int:
        return self.names.index("black")

    def index_of(self, name: str) -> int:
        return self.names.index(name)

    def with_overrides(self, overrides: dict[str, RGB]) -> Palette:
        """Return a copy with named inks replaced by measured values."""
        colors = list(self.colors)
        for name, rgb in overrides.items():
            if name not in self.names:
                raise KeyError(f"{name!r} is not an ink in the {self.scheme.value} palette")
            colors[self.names.index(name)] = tuple(int(c) for c in rgb)  # type: ignore[assignment]
        return Palette(self.scheme, tuple(colors), self.names)

    def flat(self) -> list[int]:
        """Flattened RGB list, as PIL's ``putpalette`` wants it."""
        return [c for rgb in self.colors for c in rgb]


def _greyscale_palette(scheme: ColorScheme, levels: int) -> Palette:
    ramp = _grey_ramp(levels)
    if levels == 2:
        names = ("black", "white")
    else:
        names = tuple(["black"] + [f"grey{i}" for i in range(1, levels - 1)] + ["white"])
    return Palette(scheme, ramp, names)


_BUILDERS = {
    ColorScheme.MONO: lambda: _greyscale_palette(ColorScheme.MONO, 2),
    ColorScheme.GRAY4: lambda: _greyscale_palette(ColorScheme.GRAY4, 4),
    ColorScheme.GRAY8: lambda: _greyscale_palette(ColorScheme.GRAY8, 8),
    ColorScheme.GRAY16: lambda: _greyscale_palette(ColorScheme.GRAY16, 16),
    ColorScheme.BWR: lambda: Palette(
        ColorScheme.BWR,
        (INK_BLACK, INK_WHITE, INK_RED),
        ("black", "white", "red"),
    ),
    ColorScheme.BWY: lambda: Palette(
        ColorScheme.BWY,
        (INK_BLACK, INK_WHITE, INK_YELLOW),
        ("black", "white", "yellow"),
    ),
    ColorScheme.BWRY: lambda: Palette(
        ColorScheme.BWRY,
        (INK_BLACK, INK_WHITE, INK_YELLOW, INK_RED),
        ("black", "white", "yellow", "red"),
    ),
    # Spectra 6 colour codes follow the panel's native ordering.
    ColorScheme.SPECTRA6: lambda: Palette(
        ColorScheme.SPECTRA6,
        (INK_BLACK, INK_WHITE, INK_YELLOW, INK_RED, INK_BLUE, INK_GREEN),
        ("black", "white", "yellow", "red", "blue", "green"),
    ),
    ColorScheme.ACEP7: lambda: Palette(
        ColorScheme.ACEP7,
        (INK_BLACK, INK_WHITE, INK_GREEN, INK_BLUE, INK_RED, INK_YELLOW, INK_ORANGE),
        ("black", "white", "green", "blue", "red", "yellow", "orange"),
    ),
}


def get_palette(scheme: ColorScheme | str, overrides: dict[str, RGB] | None = None) -> Palette:
    """Return the palette for a colour scheme, optionally with measured overrides."""
    scheme = ColorScheme(scheme)
    palette = _BUILDERS[scheme]()
    if overrides:
        palette = palette.with_overrides(overrides)
    return palette


__all__ = ["ColorScheme", "Palette", "get_palette", "RGB"]
