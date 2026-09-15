"""A card's own 13 px text must land at a legible physical size on every panel.

Home Assistant hard-codes pixel font sizes, so a `font-size` rule never reaches
inside a card. `build_css` scales the whole document instead, with one root
`zoom` derived from the panel's dpi, and the figure that matters is the one in
millimetres: 13 px of a card's own type is about 3 mm of ink whether the panel
is a 111 dpi shelf label or a 300 dpi Kindle.

The zoom is read out of the stylesheet rather than recomputed here, so what is
measured is what ships. `eink/theme.py` is the only place the number is decided.
"""

from __future__ import annotations

import re

import pytest

from maverick.eink.theme import ThemeOptions, build_css, root_zoom

#: A size the frontend picks itself, inside a card the stylesheet cannot reach.
CARD_TEXT_PX = 13

#: The millimetre figures the prototype measured, per panel dpi.
MEASURED_MM = {111: 2.97, 124: 3.04, 300: 2.99}

#: Tolerance in mm. Generous next to the ±0.01 the figures agree to today, so
#: the test fails on a design change rather than on a rounding one.
TOLERANCE_MM = 0.05

_ZOOM = re.compile(r"^\s*zoom:\s*([0-9.]+);\s*$", re.MULTILINE)


def css_zoom(css: str) -> float:
    """The root `zoom` the stylesheet actually declares."""
    matches = _ZOOM.findall(css)
    assert len(matches) == 1, f"expected exactly one root zoom, found {matches}"
    return float(matches[0])


def physical_mm(px: float, zoom: float, dpi: int) -> float:
    return px * zoom / dpi * 25.4


@pytest.mark.parametrize(("dpi", "expected_mm"), sorted(MEASURED_MM.items()))
def test_card_text_lands_at_the_measured_physical_size(dpi: int, expected_mm: float) -> None:
    zoom = css_zoom(build_css(ThemeOptions(dpi=dpi)))

    assert physical_mm(CARD_TEXT_PX, zoom, dpi) == pytest.approx(expected_mm, abs=TOLERANCE_MM)


@pytest.mark.parametrize("dpi", sorted(MEASURED_MM))
def test_the_stylesheet_declares_the_zoom_the_helper_computes(dpi: int) -> None:
    """`root_zoom` is the number in the CSS, not a second implementation of it."""
    options = ThemeOptions(dpi=dpi)

    # The declaration is rounded to four places; nothing else may differ.
    assert css_zoom(build_css(options)) == pytest.approx(root_zoom(options), abs=5e-5)


@pytest.mark.parametrize("dpi", sorted(MEASURED_MM))
def test_body_type_is_the_target_millimetre_size_on_every_panel(dpi: int) -> None:
    """The scale is anchored on `body_mm`: 14 reference px is that size in ink."""
    options = ThemeOptions(dpi=dpi)
    zoom = css_zoom(build_css(options))

    body_mm = physical_mm(14, zoom, dpi)

    assert body_mm == pytest.approx(options.type_scale.body_mm, abs=0.12), (
        "body text should be within a rounded pixel of the 3.2 mm target"
    )


def test_render_zoom_scales_the_physical_size_with_it() -> None:
    """`render.zoom` is a fit knob, and it moves the millimetres with it."""
    dpi = 124
    plain = css_zoom(build_css(ThemeOptions(dpi=dpi)))
    scaled = css_zoom(build_css(ThemeOptions(dpi=dpi, zoom_multiplier=1.5)))

    assert physical_mm(CARD_TEXT_PX, scaled, dpi) == pytest.approx(
        physical_mm(CARD_TEXT_PX, plain, dpi) * 1.5, abs=TOLERANCE_MM
    )
