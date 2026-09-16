"""How much a panel holds: columns, characters and lines.

This is the arithmetic behind section 7 of [the design
guide](../../../docs/design-guide.md) — "how many columns fit" — and it was
written for that page, in ``scripts/design_tables.py``, before anything but a
document needed it. Two things do now: the docs script still prints its table
from here, and :mod:`maverick.lovelace` sizes a starter dashboard from it, so
the layout a user is handed and the layout the guide describes cannot
disagree.

The question it answers is the one that governs an e-ink dashboard and no
other kind. A phone reflows and a monitor scrolls; a panel is a fixed
rectangle of a fixed physical size, and what decides how much fits on it is
*millimetres*, not pixels — a 300 dpi Kindle at 1072×1448 holds one column of
text where a 124 dpi 7.5-inch panel at 800×480 holds three, because the Kindle
is 91 mm across. Everything below is in physical units for that reason, and
converts to pixels only at the end.

Nothing here reads a file or a display: it takes a viewport and a dpi and
returns numbers, which is what lets `scripts/design_tables.py` run it over
every catalogue entry and the generator run it over one.
"""

from __future__ import annotations

from dataclasses import dataclass

from .theme import TypeScale, mm_to_px

#: Line height from the `html, body` rule in `theme.build_css`.
LINE_HEIGHT = 1.35

#: Mean advance width of a lowercase alphabet, as a fraction of the font size.
#: Measured with Pillow on the two faces from `EINK_FONT_STACK` that Linux
#: container images carry: 0.554 for DejaVu Sans, 0.482 for Liberation Sans.
#: 0.5 is the midpoint and the figure the column arithmetic uses.
MEAN_ADVANCE_RATIO = 0.5

#: A column narrower than this many characters of body text cannot hold a
#: label and a value on one line, which is what a dashboard column is for.
MIN_CHARS_PER_COLUMN = 24

#: Space inside a card edge, between cards, and around the frame, in mm. Each
#: is two rule widths plus a little; below this, cards read as one grey block.
CARD_PADDING_MM = 2.0
GUTTER_MM = 2.0
MARGIN_MM = 2.0


@dataclass(frozen=True)
class LayoutBudget:
    """What one panel holds, at the theme's own body size.

    ``lines`` is the number that actually constrains a dashboard, and the one
    people are most surprised by: it counts *body lines for the whole frame*,
    headings and blank space included. A 2.9-inch shelf label gets five.
    """

    #: The viewport the browser lays the page out in, before any rotation.
    width: int
    height: int
    dpi: int
    #: Body text size in device pixels, from `TypeScale.px`.
    body_px: int
    #: Columns of at least `MIN_CHARS_PER_COLUMN` characters that fit side by side.
    columns: int
    #: Width of one of those columns, in device pixels.
    column_px: int
    #: Characters of body text one column holds on a line.
    chars_per_column: int
    #: Body lines the full height holds, at `LINE_HEIGHT`.
    lines: int

    @property
    def width_mm(self) -> float:
        return self.width * 25.4 / self.dpi

    @property
    def height_mm(self) -> float:
        return self.height * 25.4 / self.dpi

    def describe(self) -> str:
        """One sentence a setup UI can show without doing arithmetic of its own."""
        column_word = "column" if self.columns == 1 else "columns"
        return (
            f"{self.width}×{self.height} at {self.dpi} dpi is "
            f"{self.width_mm:.0f}×{self.height_mm:.0f} mm: "
            f"{self.columns} {column_word} of about {self.chars_per_column} characters, "
            f"{self.lines} lines of body text in total."
        )


def budget_for(width: int, height: int, dpi: int, scale: TypeScale | None = None) -> LayoutBudget:
    """The layout budget for a viewport, at ``dpi``.

    ``width`` and ``height`` are the *browser viewport*, which is what the
    layout sees — a panel with a `native_rotation` of 90 or 270 is rendered
    transposed and rotated afterwards, so a 152×296 shelf label lays out as
    296×152 (`DashboardRenderer.viewport_for`,
    `src/maverick/render/dashboard.py`).
    """
    scale = scale or TypeScale()
    body = scale.px(dpi, 0)
    advance = body * MEAN_ADVANCE_RATIO
    padding = mm_to_px(CARD_PADDING_MM, dpi)
    gutter = mm_to_px(GUTTER_MM, dpi)
    margin = mm_to_px(MARGIN_MM, dpi)

    usable_w = width - 2 * margin
    usable_h = height - 2 * margin
    wanted = MIN_CHARS_PER_COLUMN * advance + 2 * padding
    columns = max(1, int((usable_w + gutter) // (wanted + gutter)))
    column_px = (usable_w - (columns - 1) * gutter) / columns
    chars = max(0, int((column_px - 2 * padding) // advance))
    lines = max(0, int(usable_h // (body * LINE_HEIGHT)))
    return LayoutBudget(
        width=width,
        height=height,
        dpi=dpi,
        body_px=body,
        columns=columns,
        column_px=round(column_px),
        chars_per_column=chars,
        lines=lines,
    )


__all__ = [
    "CARD_PADDING_MM",
    "GUTTER_MM",
    "LINE_HEIGHT",
    "MARGIN_MM",
    "MEAN_ADVANCE_RATIO",
    "MIN_CHARS_PER_COLUMN",
    "LayoutBudget",
    "budget_for",
]
