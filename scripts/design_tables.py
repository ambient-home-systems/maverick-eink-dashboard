#!/usr/bin/env python3
"""Compute every number that appears in ``docs/design-guide.md``.

The design guide is the page the render linter points people at, so a figure in
it that disagrees with the code is worse than no figure at all. Nothing in that
page is typed by hand: every table below is printed from the same functions the
renderer runs — :class:`maverick.eink.theme.TypeScale`, :func:`build_css`,
:func:`maverick.eink.dither.quantize`, :func:`maverick.eink.lint.lint_frame` and
the palettes in :mod:`maverick.eink.palette` — against the dpi and resolution
columns of ``src/maverick/devices/panels.yaml``.

Run it after changing a default in ``eink/`` and paste the tables back:

    python scripts/design_tables.py                # every table
    python scripts/design_tables.py type-scale     # one table
    python scripts/design_tables.py --list         # the table names

Two tables (``dither`` and ``hairline``) quantise synthetic test cards rather
than deriving a figure from arithmetic, because what a check like
``dither_speckle`` measures is only legible as a number you can watch move.
Their text rows need a TrueType face from the theme's own fallback stack; DejaVu
Sans is the one Linux container images reliably carry. Without it those rows are
skipped and the table says so — every other table is pure arithmetic and depends
on nothing outside this repository.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import yaml  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from maverick.eink.dither import DitherMode, _continuous_tone_mask, quantize  # noqa: E402
from maverick.eink.lint import LintThresholds, lint_frame  # noqa: E402
from maverick.eink.palette import (  # noqa: E402
    INK_BLACK,
    INK_BLUE,
    INK_GREEN,
    INK_ORANGE,
    INK_RED,
    INK_WHITE,
    INK_YELLOW,
    ColorScheme,
    get_palette,
)
from maverick.eink.theme import (  # noqa: E402
    REFERENCE_BASE_PX,
    ThemeOptions,
    TypeScale,
    build_css,
    mm_to_px,
)

PANELS = ROOT / "src" / "maverick" / "devices" / "panels.yaml"

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

#: Home Assistant hard-codes this size on a good deal of card text. The theme
#: never reaches it with a `font-size` rule; root `zoom` scales it anyway.
HA_CARD_TEXT_PX = 13

_FONT_NAMES = ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
_FONT_DIRS = (
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".fonts",
)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def panels() -> list[dict]:
    return yaml.safe_load(PANELS.read_text(encoding="utf-8"))["panels"]


def dpi_values() -> list[int]:
    return sorted({int(p["dpi"]) for p in panels()})


def zoom_for(dpi: int, scale: TypeScale | None = None) -> float:
    """The root `zoom` `build_css` derives for a panel, with no multiplier."""
    scale = scale or TypeScale()
    return scale.px(dpi, 0) / REFERENCE_BASE_PX


def device_px(reference_px: float, dpi: int) -> float:
    """A size written in the stylesheet, as pixels on the panel."""
    return reference_px * zoom_for(dpi)


def px_to_mm(px: float, dpi: int) -> float:
    return px * 25.4 / dpi


def table(header: list[str], rows: list[list[str]]) -> list[str]:
    return [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
        *["| " + " | ".join(r) + " |" for r in rows],
    ]


def caption(text: str) -> list[str]:
    """A table's note, italicised so it reads as a caption in the guide."""
    return [f"_{' '.join(text.split())}_", ""]


def _find_font(name: str) -> Path | None:
    for directory in _FONT_DIRS:
        if not directory.is_dir():
            continue
        for found in directory.rglob(name):
            return found
    return None


def fonts() -> tuple[Path, Path] | None:
    """The regular and bold faces the text probes need, or None."""
    found = [_find_font(n) for n in _FONT_NAMES]
    if all(f is not None for f in found):
        return found[0], found[1]  # type: ignore[return-value]
    return None


# --------------------------------------------------------------------------- #
# 1. Type scale
# --------------------------------------------------------------------------- #


def type_scale() -> list[str]:
    """Body, small, large and xlarge in panel pixels, for every dpi in use.

    `TypeScale.px` is the physical target: millimetres through the panel's dpi,
    floored at `min_px`. The stylesheet reaches it a different way — it writes
    reference pixels and sets a root `zoom` — so the last two columns show what
    that actually delivers, and the two agree to within a rounding step.
    """
    scale = TypeScale()
    by_dpi: dict[int, list[str]] = defaultdict(list)
    for panel in panels():
        by_dpi[int(panel["dpi"])].append(panel["id"])

    rows = []
    for dpi in dpi_values():
        exact = mm_to_px(scale.body_mm, dpi)
        zoom = zoom_for(dpi, scale)
        rows.append(
            [
                str(dpi),
                f"{exact:.2f}",
                str(scale.px(dpi, -1)),
                f"**{scale.px(dpi, 0)}**",
                str(scale.px(dpi, 1)),
                str(scale.px(dpi, 2)),
                f"{zoom:.4f}",
                f"{px_to_mm(scale.px(dpi, 0), dpi):.2f}",
                f"{px_to_mm(device_px(HA_CARD_TEXT_PX, dpi), dpi):.2f}",
                str(len(by_dpi[dpi])),
            ]
        )
    return [
        *caption(
            f"`body_mm` {scale.body_mm}, `ratio` {scale.ratio},"
            f" `min_px` {scale.min_px}; reference base"
            f" {REFERENCE_BASE_PX:.0f} px."
        ),
        *table(
            [
                "dpi",
                "3.2 mm in px",
                "small",
                "body",
                "large",
                "xlarge",
                "zoom",
                "body mm",
                "HA's 13 px in mm",
                "panels",
            ],
            rows,
        ),
    ]


# --------------------------------------------------------------------------- #
# 2. Physical measures
# --------------------------------------------------------------------------- #


def physical() -> list[str]:
    """What one pixel is worth, and what the theme's mm-sized rules become.

    `build_css` computes the border width as `max(1.0, rule_mm in reference px)`.
    Because zoom is itself derived from `body_mm`, the reference-space rule is
    near enough constant across panels — the millimetre, not the pixel, is what
    is being held fixed.
    """
    options = ThemeOptions()
    thresholds = LintThresholds()
    rows = []
    for dpi in dpi_values():
        zoom = zoom_for(dpi)
        pixel_mm = 25.4 / dpi
        rule_ref = round(max(1.0, mm_to_px(options.rule_mm, dpi) / zoom), 2)
        rule_px = rule_ref * zoom
        tracking = 0.012 if dpi < 150 else 0.0
        stroke = max(0.2, 0.6 if dpi < 150 else 0.3)
        rows.append(
            [
                str(dpi),
                f"{pixel_mm:.4f}",
                f"{rule_ref:.2f}",
                f"{rule_px:.2f}",
                f"{px_to_mm(rule_px, dpi):.3f}",
                f"{tracking:.3f}",
                f"{stroke:.2f}",
                "yes" if pixel_mm < thresholds.min_feature_mm else "no",
            ]
        )
    return [
        *caption(
            f"`rule_mm` {options.rule_mm}, `radius_mm` {options.radius_mm},"
            f" `min_feature_mm` {thresholds.min_feature_mm}."
        ),
        *table(
            [
                "dpi",
                "1 px in mm",
                "rule (ref px)",
                "rule (panel px)",
                "rule in mm",
                "tracking (em)",
                "icon stroke (ref px)",
                "sub_threshold_pixel",
            ],
            rows,
        ),
    ]


# --------------------------------------------------------------------------- #
# 3. Measured inks
# --------------------------------------------------------------------------- #


def inks() -> list[str]:
    """The measured ink constants, with their luminance against the panel white."""
    named = [
        ("white", INK_WHITE),
        ("black", INK_BLACK),
        ("red", INK_RED),
        ("yellow", INK_YELLOW),
        ("blue", INK_BLUE),
        ("green", INK_GREEN),
        ("orange", INK_ORANGE),
    ]
    weights = np.array([0.299, 0.587, 0.114], dtype=np.float32)
    white_lum = float(np.asarray(INK_WHITE, dtype=np.float32) @ weights)
    rows = []
    for name, rgb in named:
        lum = float(np.asarray(rgb, dtype=np.float32) @ weights)
        used = [s.value for s in ColorScheme if name in get_palette(s).names]
        rows.append(
            [
                f"`{name}`",
                f"`{rgb[0]}, {rgb[1]}, {rgb[2]}`",
                f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}",
                f"{lum:.0f}",
                f"{white_lum / lum:.1f}:1" if lum else "—",
                ", ".join(f"`{u}`" for u in used) or "—",
            ]
        )
    return [
        *caption(
            "Luminance uses the 0.299/0.587/0.114 weighting"
            " `dither._nearest_indices` quantises with; the ratio is against the"
            " panel's own white, not #ffffff."
        ),
        *table(
            ["ink", "measured RGB", "hex", "luminance", "contrast vs white", "schemes"],
            rows,
        ),
    ]


# --------------------------------------------------------------------------- #
# 4. Schemes
# --------------------------------------------------------------------------- #


def schemes() -> list[str]:
    """Per colour scheme: inks, packing width, and which lint checks can fire."""
    rows = []
    for scheme in ColorScheme:
        palette = get_palette(scheme)
        pal = np.asarray(palette.colors, dtype=np.float32)
        spread = float(np.mean(np.sort(np.linalg.norm(pal[:, None] - pal[None, :], axis=2))[:, 1]))
        spot = [n for n in ("red", "yellow", "orange") if n in palette.names]
        rows.append(
            [
                f"`{scheme.value}`",
                str(len(palette)),
                str(palette.bits_per_pixel),
                ", ".join(f"`{n}`" for n in palette.names),
                f"{spread:.0f}",
                ", ".join(f"`{s}`" for s in spot) or "—",
                "yes" if len(palette) > 4 else "no",
            ]
        )
    return [
        *caption(
            "`spread` is the mean nearest-neighbour distance between inks — the"
            " threshold noise `dither._ordered` scales its Bayer matrix by, and"
            " so a direct measure of how violently ordered dithering treats a"
            " flat fill."
        ),
        *table(
            [
                "scheme",
                "inks",
                "bits/px",
                "names",
                "spread",
                "spot checks",
                "`palette_underused` can fire",
            ],
            rows,
        ),
    ]


# --------------------------------------------------------------------------- #
# 5. Columns per resolution
# --------------------------------------------------------------------------- #


def _columns_for(width: int, height: int, dpi: int) -> tuple[int, int, int, int]:
    """Columns, characters per column, body lines, and the column width in px."""
    body = TypeScale().px(dpi, 0)
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
    return columns, chars, lines, round(column_px)


def columns() -> list[str]:
    """How many readable columns each catalogued resolution holds.

    Mechanism, not taste: a column has to fit a label and its value on one line.
    At `MIN_CHARS_PER_COLUMN` characters and a mean advance of half the body
    size, the minimum column is a fixed number of body pixels wide; everything
    else is the panel's own width divided by it.

    The width that matters is the browser viewport, which for a panel with a
    `native_rotation` of 90 or 270 is the panel transposed — see
    `DashboardRenderer.viewport_for`. A portrait shelf label lays out landscape.
    """
    grouped: dict[tuple[int, int, int], list[str]] = defaultdict(list)
    for panel in panels():
        width, height = int(panel["width"]), int(panel["height"])
        rotation = int(panel.get("native_rotation", 0))
        label = panel["id"]
        if rotation in (90, 270):
            width, height = height, width
            label = f"{label} (rotated {rotation}°)"
        grouped[(width, height, int(panel["dpi"]))].append(label)

    rows = []
    for (width, height, dpi), ids in sorted(grouped.items()):
        cols, chars, lines, column_px = _columns_for(width, height, dpi)
        rows.append(
            [
                f"{width}×{height}",
                str(dpi),
                str(TypeScale().px(dpi, 0)),
                str(cols),
                str(column_px),
                str(chars),
                str(lines),
                ", ".join(f"`{i}`" for i in ids),
            ]
        )
    return [
        *caption(
            f"{MIN_CHARS_PER_COLUMN} characters minimum per column, mean advance"
            f" {MEAN_ADVANCE_RATIO} × body px, {CARD_PADDING_MM} mm card padding,"
            f" {GUTTER_MM} mm gutters, {MARGIN_MM} mm outer margin, line height"
            f" {LINE_HEIGHT}. Sizes are browser viewports, so a panel with a"
            f" native rotation appears transposed."
        ),
        *table(
            [
                "viewport",
                "dpi",
                "body px",
                "columns",
                "column px",
                "chars/column",
                "body lines",
                "panels",
            ],
            rows,
        ),
    ]


# --------------------------------------------------------------------------- #
# 6. Dither probe
# --------------------------------------------------------------------------- #

_PROBE_W, _PROBE_H = 400, 240


def _patch_text(font: Path | None) -> Image.Image | None:
    if font is None:
        return None
    image = Image.new("RGB", (_PROBE_W, _PROBE_H), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    face = ImageFont.truetype(str(font), 16)
    for i in range(6):
        draw.text((12, 12 + i * 34), "Living room 21.4 C  Humidity 48%", font=face, fill=(0, 0, 0))
    return image


def _patch_gradient() -> Image.Image:
    ramp = np.tile(np.linspace(0, 255, _PROBE_W), (_PROBE_H, 1)).astype(np.uint8)
    return Image.fromarray(np.dstack([ramp] * 3))


def _patch_gauge() -> Image.Image:
    image = Image.new("RGB", (_PROBE_W, _PROBE_H), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for i in range(60):
        value = int(255 * i / 59)
        inset = i // 4
        draw.arc(
            [60 + inset, 20 + inset, 340 - inset, 220 - inset],
            200,
            340,
            fill=(value, value, value),
            width=3,
        )
    return image


def _patch_sparkline() -> Image.Image:
    image = Image.new("RGB", (_PROBE_W, _PROBE_H), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    points = [(x, 120 + int(60 * math.sin(x / 23.0))) for x in range(0, _PROBE_W, 4)]
    draw.polygon([*points, (_PROBE_W, _PROBE_H), (0, _PROBE_H)], fill=(200, 200, 200))
    draw.line(points, fill=(120, 120, 120), width=2)
    return image


def _patch_photo() -> Image.Image:
    rng = np.random.default_rng(7)
    base = rng.normal(128, 40, (_PROBE_H // 8, _PROBE_W // 8, 3)).clip(0, 255).astype(np.uint8)
    return Image.fromarray(base).resize((_PROBE_W, _PROBE_H), Image.BICUBIC)


def dither() -> list[str]:
    """What `AUTO` decides about each kind of dashboard content, and what it costs.

    The mask column is the fraction of the patch `dither._continuous_tone_mask`
    marks as a broad midtone field — the only part of the image `AUTO` diffuses
    error through. Everything else is snapped to the nearest ink, which is why
    the flat card's `auto` and `none` columns are identical.
    """
    face = fonts()
    patches = [
        ("flat card, 16 px text", _patch_text(face[0] if face else None)),
        ("gauge arc (grey ramp)", _patch_gauge()),
        ("sparkline with fill", _patch_sparkline()),
        ("linear gradient", _patch_gradient()),
        ("camera thumbnail", _patch_photo()),
    ]
    palette = get_palette(ColorScheme.MONO)
    limit = LintThresholds().max_speckle_ratio

    rows = []
    for name, patch in patches:
        if patch is None:
            rows.append([name, "—", "—", "—", "—", "—", "—", "_skipped: no DejaVu Sans_"])
            continue
        mask = _continuous_tone_mask(np.asarray(patch.convert("RGB"), dtype=np.float32))
        speckle, ink = {}, {}
        for mode in (DitherMode.AUTO, DitherMode.NONE, DitherMode.ORDERED):
            report = lint_frame(quantize(patch, palette, mode), palette, 124)
            speckle[mode] = report.metrics["speckle_ratio"]
            ink[mode] = report.metrics["coverage.ink"]
        auto = speckle[DitherMode.AUTO]
        rows.append(
            [
                name,
                f"{mask.mean():.3f}",
                f"**{auto:.4f}**" if auto > limit else f"{auto:.4f}",
                f"{speckle[DitherMode.NONE]:.4f}",
                f"{speckle[DitherMode.ORDERED]:.4f}",
                f"{ink[DitherMode.AUTO]:.3f}",
                f"{ink[DitherMode.NONE]:.3f}",
                f"{ink[DitherMode.ORDERED]:.3f}",
            ]
        )
    return [
        *caption(
            f"{_PROBE_W}×{_PROBE_H} patches quantised against the `mono` palette."
            f" Speckle is `speckle_ratio` from `lint_frame`; **bold** exceeds the"
            f" {limit} threshold. Ink is `coverage.ink` — the fraction of the"
            " patch that is not white."
        ),
        *table(
            [
                "patch",
                "midtone mask",
                "speckle `auto`",
                "speckle `none`",
                "speckle `ordered`",
                "ink `auto`",
                "ink `none`",
                "ink `ordered`",
            ],
            rows,
        ),
    ]


# --------------------------------------------------------------------------- #
# 7. Hairline probe
# --------------------------------------------------------------------------- #


def _bars(width_px: int) -> Image.Image:
    image = Image.new("RGB", (_PROBE_W, _PROBE_H), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for x in range(20, _PROBE_W - 20, width_px * 4):
        draw.rectangle([x, 20, x + width_px - 1, _PROBE_H - 20], fill=(0, 0, 0))
    return image


def _text_block(font: Path, size: int) -> Image.Image:
    image = Image.new("RGB", (_PROBE_W, _PROBE_H), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    face = ImageFont.truetype(str(font), size)
    y = 10
    while y < _PROBE_H - size:
        draw.text((10, y), "Living room 21.4 C", font=face, fill=(0, 0, 0))
        y += int(size * LINE_HEIGHT * 1.6)
    return image


def hairline() -> list[str]:
    """What `hairline_ratio` actually measures, on strokes of a known width.

    `lint._erode` keeps an inked pixel only when all eight of its neighbours are
    inked too, so the metric is the fraction of the inked set that lies on its
    own boundary. For a long stroke `w` pixels wide that is exactly `2/w`, and
    the 0.28 threshold therefore asks for a mean stroke around 7 px.
    """
    palette = get_palette(ColorScheme.MONO)
    limit = LintThresholds().max_hairline_ratio
    rows = []
    for width_px in (1, 2, 3, 4, 6, 8, 12):
        report = lint_frame(quantize(_bars(width_px), palette, DitherMode.NONE), palette, 124)
        measured = report.metrics["hairline_ratio"]
        rows.append(
            [
                f"{width_px} px bars",
                f"{measured:.3f}",
                "1.000" if width_px < 3 else f"{2 / width_px:.3f}",
                "**over**" if measured > limit else "under",
            ]
        )

    face = fonts()
    if face is None:
        rows.append(["real text", "—", "—", "_skipped: DejaVu Sans not installed_"])
    else:
        for regular, size in ((True, 16), (True, 28), (False, 16), (False, 28)):
            path = face[0] if regular else face[1]
            report = lint_frame(
                quantize(_text_block(path, size), palette, DitherMode.AUTO), palette, 124
            )
            measured = report.metrics["hairline_ratio"]
            label = "regular" if regular else "bold"
            rows.append(
                [
                    f"{size} px text, {label}",
                    f"{measured:.3f}",
                    "—",
                    "**over**" if measured > limit else "under",
                ]
            )
    return [
        *caption(
            f"`hairline_ratio` from `lint_frame`, threshold {limit}. Bars are"
            " quantised with `dither: none` so the figure is geometry alone."
        ),
        *table(["subject", "measured", "2/w", "vs threshold"], rows),
    ]


# --------------------------------------------------------------------------- #
# 8. Worked example
# --------------------------------------------------------------------------- #


def worked_example(width: int = 800, height: int = 480, dpi: int = 124) -> list[str]:
    """Every figure for one panel: the 800×480 mono 7.5", the catalogue's default."""
    scheme = ColorScheme.MONO
    scale = TypeScale()
    options = ThemeOptions(dpi=dpi, scheme=scheme)
    palette = get_palette(scheme)
    thresholds = LintThresholds()
    zoom = zoom_for(dpi, scale)
    cols, chars, lines, column_px = _columns_for(width, height, dpi)

    def line(label: str, value: str, how: str) -> list[str]:
        return [label, value, how]

    steps = [
        line(
            "panel",
            f"{width}×{height} px, {px_to_mm(width, dpi):.0f}×{px_to_mm(height, dpi):.0f} mm",
            f"{width}/{dpi} in × 25.4",
        ),
        line("one pixel", f"{25.4 / dpi:.4f} mm", f"25.4 / {dpi}"),
        line(
            "body target",
            f"{mm_to_px(scale.body_mm, dpi):.2f} px → {scale.px(dpi, 0)} px",
            f"{scale.body_mm} × {dpi} / 25.4, rounded, floored at {scale.min_px}",
        ),
        line(
            "root zoom",
            f"{zoom:.4f}",
            f"{scale.px(dpi, 0)} / {REFERENCE_BASE_PX:.0f}",
        ),
    ]
    for label, step in (("small", -1), ("body", 0), ("large", 1), ("xlarge", 2), ("huge", 3)):
        reference = round(REFERENCE_BASE_PX * (scale.ratio**step), 1)
        panel_px = reference * zoom
        steps.append(
            line(
                label,
                f"{reference} ref px → {panel_px:.1f} px → {px_to_mm(panel_px, dpi):.2f} mm",
                f"{REFERENCE_BASE_PX:.0f} × {scale.ratio}^{step}, × zoom",
            )
        )
    rule_ref = round(max(1.0, mm_to_px(options.rule_mm, dpi) / zoom), 2)
    steps += [
        line(
            "card rule",
            f"{rule_ref} ref px → {rule_ref * zoom:.2f} px →"
            f" {px_to_mm(rule_ref * zoom, dpi):.3f} mm",
            f"max(1.0, {options.rule_mm} mm in px / zoom)",
        ),
        line("card radius", f"{options.radius_mm:.1f} px", f"radius_mm {options.radius_mm}"),
        line("tracking", "0.012 em", f"dpi {dpi} < 150"),
        line("icon stroke", "0.60 ref px", f"dpi {dpi} < 150"),
        line(
            "icon size",
            f"{round(REFERENCE_BASE_PX * scale.ratio, 1)} ref px →"
            f" {px_to_mm(device_px(round(REFERENCE_BASE_PX * scale.ratio, 1), dpi), dpi):.2f} mm",
            "--mdc-icon-size is the `large` step",
        ),
        line(
            "HA's own 13 px text",
            f"{px_to_mm(device_px(HA_CARD_TEXT_PX, dpi), dpi):.2f} mm",
            f"{HA_CARD_TEXT_PX} × zoom × 25.4 / {dpi}",
        ),
        line(
            "body line",
            f"{scale.px(dpi, 0) * LINE_HEIGHT:.1f} px →"
            f" {px_to_mm(scale.px(dpi, 0) * LINE_HEIGHT, dpi):.2f} mm",
            f"body px × line-height {LINE_HEIGHT}",
        ),
        line("columns", f"{cols} × {column_px} px", f"{chars} chars each"),
        line("body lines", str(lines), "usable height / line height"),
        line(
            "palette",
            f"{len(palette)} inks, {palette.bits_per_pixel} bit/px,"
            f" {width * height * palette.bits_per_pixel // 8} B packed",
            ", ".join(palette.names),
        ),
        line(
            "checks that can fire",
            "`blank_render`, `heavy_ink`, `hairlines`, `dither_speckle`",
            f"no spot ink; {len(palette)} inks ≤ 4;"
            f" 1 px = {25.4 / dpi:.4f} mm ≥ {thresholds.min_feature_mm}",
        ),
    ]

    header = build_css(options).splitlines()[1:8]
    return [
        *table(["quantity", "value", "from"], steps),
        "",
        "The stylesheet says the same thing in its own header:",
        "",
        "```css",
        *[h.rstrip() for h in header],
        "```",
    ]


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

TABLES = {
    "type-scale": type_scale,
    "physical": physical,
    "inks": inks,
    "schemes": schemes,
    "columns": columns,
    "dither": dither,
    "hairline": hairline,
    "worked-example": worked_example,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("table", nargs="*", choices=[*TABLES, []], help="tables to print")
    parser.add_argument("--list", action="store_true", help="list the table names")
    args = parser.parse_args(argv)

    if args.list:
        for name, fn in TABLES.items():
            summary = (fn.__doc__ or "").strip().splitlines()[0]
            print(f"{name:<15} {summary}")
        return 0

    wanted = args.table or list(TABLES)
    for name in wanted:
        print(f"## {name}\n")
        print("\n".join(TABLES[name]()))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
