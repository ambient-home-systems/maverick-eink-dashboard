"""A panel's spot pigments, in the linter and in the theme.

Two things keyed off the literal name ``red`` and quietly did nothing without
it. The linter checked coverage for ``red``, ``yellow`` and ``orange`` only,
so blue and green on a Spectra 6 panel — the same particle population, the same
refresh cost — were never counted. And the theme resolved its accent by looking
up ``red`` with the foreground as a fallback, so a BWY panel's one spot pigment
went unused and its alerts came out in plain black.
"""

from __future__ import annotations

import numpy as np

from maverick.eink.lint import lint_frame
from maverick.eink.palette import ColorScheme, get_palette
from maverick.eink.theme import ThemeOptions, build_css


def _frame(scheme: ColorScheme, ink: str, coverage: float) -> np.ndarray:
    palette = get_palette(scheme)
    frame = np.full((100, 100), palette.white_index, dtype=np.uint8)
    frame[: int(100 * coverage)] = palette.index_of(ink)
    return frame


# --------------------------------------------------------------------------- #
# The linter
# --------------------------------------------------------------------------- #


def test_spot_inks_are_the_pigments_beyond_black_and_white() -> None:
    assert get_palette(ColorScheme.MONO).spot_inks == ()
    assert get_palette(ColorScheme.BWR).spot_inks == ("red",)
    assert get_palette(ColorScheme.BWY).spot_inks == ("yellow",)
    assert set(get_palette(ColorScheme.SPECTRA6).spot_inks) == {
        "yellow",
        "red",
        "blue",
        "green",
    }


def test_a_grey_ramp_has_no_spot_inks() -> None:
    """Greys are the same pigment at another voltage, so they cost nothing extra."""
    assert get_palette(ColorScheme.GRAY16).spot_inks == ()

    report = lint_frame(_frame(ColorScheme.GRAY16, "grey7", 0.5), get_palette(ColorScheme.GRAY16))

    assert not [i for i in report.issues if i.code.startswith("spot_ink_overuse")]


def test_blue_and_green_coverage_is_reported() -> None:
    palette = get_palette(ColorScheme.SPECTRA6)

    for ink in ("blue", "green"):
        report = lint_frame(_frame(ColorScheme.SPECTRA6, ink, 0.30), palette)
        codes = [i.code for i in report.issues]
        assert f"spot_ink_overuse.{ink}" in codes, codes


def test_coverage_under_the_threshold_is_not_reported() -> None:
    palette = get_palette(ColorScheme.SPECTRA6)

    report = lint_frame(_frame(ColorScheme.SPECTRA6, "blue", 0.10), palette)

    assert not [i for i in report.issues if i.code.startswith("spot_ink_overuse")]


# --------------------------------------------------------------------------- #
# The theme
# --------------------------------------------------------------------------- #


def _ink_css(scheme: ColorScheme, name: str) -> str:
    r, g, b = get_palette(scheme).colors[get_palette(scheme).index_of(name)]
    return f"rgb({r},{g},{b})"


def test_a_legible_spot_ink_still_colours_the_text() -> None:
    """Red is 3.0:1 against the panel white, so nothing about BWR changes."""
    css = build_css(ThemeOptions(scheme=ColorScheme.BWR))

    assert f"color: {_ink_css(ColorScheme.BWR, 'red')} !important;" in css


def test_a_pale_spot_ink_becomes_the_ground_instead_of_the_figure() -> None:
    """Yellow is 1.3:1 as text and 6.9:1 under black, so it is used as a fill."""
    yellow = _ink_css(ColorScheme.BWY, "yellow")
    css = build_css(ThemeOptions(scheme=ColorScheme.BWY))

    assert f"background: {yellow} !important;" in css
    assert f"color: {yellow}" not in css, "yellow text on white is not readable"


def test_a_colour_panel_prefers_red_over_a_higher_contrast_blue() -> None:
    """Blue has more contrast, but it does not mean "alert"; red does."""
    for scheme in (ColorScheme.SPECTRA6, ColorScheme.ACEP7, ColorScheme.BWRY):
        css = build_css(ThemeOptions(scheme=scheme))
        assert f"--accent-color: {_ink_css(scheme, 'red')};" in css


def test_use_spot_colour_false_leaves_the_pigment_alone() -> None:
    for scheme, ink in ((ColorScheme.BWR, "red"), (ColorScheme.BWY, "yellow")):
        css = build_css(ThemeOptions(scheme=scheme, use_spot_colour=False))
        assert _ink_css(scheme, ink) not in css
