"""`render.zoom` must be applied exactly once, whatever the theme is doing.

With the theme enabled, `build_css` folds the zoom into the stylesheet's own
root zoom as `zoom_multiplier`. With it disabled, the renderer injects a style
tag instead. That choice used to be made on "did the composed stylesheet come
back empty", which is not the same question: disabling the theme while keeping
any `theme.extra_css` produced a non-empty stylesheet with no zoom in it, and
the tag was skipped, so `render.zoom` did nothing at all.
"""

from __future__ import annotations

import pytest

from maverick.config import DisplayConfig
from maverick.eink.theme import ThemeOptions, build_css
from maverick.render.dashboard import build_theme_css, needs_zoom_style_tag


def _display(**overrides: object) -> DisplayConfig:
    return DisplayConfig.model_validate({"id": "kitchen", **overrides})


@pytest.mark.parametrize("extra_css", ["", "ha-card { border: 0 }"])
def test_the_renderer_applies_the_zoom_when_the_theme_is_off(extra_css: str) -> None:
    config = _display(theme={"enabled": False, "extra_css": extra_css}, render={"zoom": 1.4})

    assert needs_zoom_style_tag(config) is True


@pytest.mark.parametrize("extra_css", ["", "ha-card { border: 0 }"])
def test_the_theme_carries_the_zoom_when_it_is_on(extra_css: str) -> None:
    config = _display(theme={"enabled": True, "extra_css": extra_css}, render={"zoom": 1.4})

    assert needs_zoom_style_tag(config) is False
    assert "zoom: 1.6000;" in build_theme_css(config.resolved()), "1.1429 x 1.4"


def test_no_tag_when_there_is_no_zoom_to_apply() -> None:
    assert needs_zoom_style_tag(_display(theme={"enabled": False})) is False
    assert needs_zoom_style_tag(_display()) is False


def test_the_multiplier_composes_with_the_dpi_derived_zoom() -> None:
    """The theme's zoom is physical; render.zoom scales it rather than replacing it."""
    plain = build_css(ThemeOptions(dpi=124))
    scaled = build_css(ThemeOptions(dpi=124, zoom_multiplier=0.5))

    assert "zoom: 1.1429;" in plain
    assert "zoom: 0.5714;" in scaled
