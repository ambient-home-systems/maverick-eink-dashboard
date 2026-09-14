"""`image.palette_overrides` must reach the stylesheet, not just quantisation.

The overrides exist so a user can key the theme to the inks their panel really
produces. They already fed PipelineOptions.palette; build_css ignored them, so
the page was styled with catalogue ink while the frame was quantised to the
measured ink — the two describing different panels.
"""

from __future__ import annotations

from maverick.config import Config
from maverick.eink.palette import ColorScheme, get_palette
from maverick.eink.theme import ThemeOptions, build_css
from maverick.render.dashboard import build_theme_css

MEASURED_RED = (198, 32, 24)


def test_build_css_uses_the_overridden_ink() -> None:
    css = build_css(
        ThemeOptions(scheme=ColorScheme.BWR, palette_overrides={"red": MEASURED_RED})
    )

    assert "rgb(198,32,24)" in css
    stock = get_palette(ColorScheme.BWR)
    r, g, b = stock.colors[stock.index_of("red")]
    assert f"rgb({r},{g},{b})" not in css, "the catalogue ink must be replaced, not joined"


def test_build_css_without_overrides_is_unchanged() -> None:
    stock = get_palette(ColorScheme.BWR)
    r, g, b = stock.colors[stock.index_of("red")]

    assert f"rgb({r},{g},{b})" in build_css(ThemeOptions(scheme=ColorScheme.BWR))


def test_config_overrides_reach_the_injected_stylesheet() -> None:
    """The same overrides drive the CSS and the quantisation palette."""
    config = Config.model_validate(
        {
            "displays": [
                {
                    "id": "kitchen",
                    "panel": "waveshare-4in2-bwr",
                    "image": {"palette_overrides": {"red": list(MEASURED_RED)}},
                }
            ]
        }
    )
    display = config.display("kitchen").resolved()

    assert "rgb(198,32,24)" in build_theme_css(display)
    assert display.config.image.palette_overrides["red"] == MEASURED_RED
