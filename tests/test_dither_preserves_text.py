"""Error diffusion must not run over text.

The prototype's finding, in one line: "dither wherever there is local contrast"
eats glyphs, because text has more local contrast than anything else on a
dashboard. `DitherMode.AUTO` classifies instead — broad fields of intermediate
tone are photographs and gradients and get diffused; everything else snaps to
the nearest ink — and the result the spike measured was crisp 1-bit text at a
speckle ratio of 0.000.

The image below is a stand-in for a dashboard: black readings, grey secondary
labels, a gradient block and a flat mid-grey fill, on white. No browser is
involved, so what is under test is the quantiser and the linter, which is where
the claim lives.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from maverick.eink.dither import DitherMode
from maverick.eink.palette import ColorScheme
from maverick.eink.pipeline import Frame, PipelineOptions, process

WIDTH, HEIGHT = 320, 200

#: Where each element of the synthetic dashboard sits. The bands are kept well
#: apart because the continuous-tone mask is dilated by two pixels, and a test
#: that measured the text right up against the gradient would be measuring the
#: dilation rather than the classification.
TEXT_BAND = slice(0, 65)
GRADIENT_BAND = slice(85, 125)
GRADIENT_COLUMNS = slice(15, 245)

#: Secondary label grey. Dark enough that the nearest ink is black, so a label
#: that survives quantisation is still readable — which is the point of the
#: claim, not merely that something was drawn.
LABEL_GREY = (120, 120, 120)

ROWS = [
    ("Kitchen", "21.4 C"),
    ("Humidity", "48 %"),
    ("Wind", "12 km/h"),
    ("Next bus", "7 min"),
    ("Bin day", "Thursday"),
]


def _font(size: int = 20) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def _draw_readings(draw: ImageDraw.ImageDraw, rows: list[tuple[str, str]], top: int) -> None:
    for index, (label, value) in enumerate(rows):
        y = top + index * 32
        draw.text((10, y), label, fill=LABEL_GREY, font=_font())
        draw.text((170, y), value, fill=(0, 0, 0), font=_font())


def dashboard_image() -> Image.Image:
    """White ground, text, a grey gradient and a flat mid-grey fill."""
    image = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    _draw_readings(draw, ROWS[:2], top=8)

    left, right = GRADIENT_COLUMNS.start, GRADIENT_COLUMNS.stop
    span = right - left
    for offset in range(span):
        value = round(255 * offset / (span - 1))
        draw.line(
            [(left + offset, GRADIENT_BAND.start - 5), (left + offset, GRADIENT_BAND.stop + 5)],
            fill=(value, value, value),
        )

    draw.rectangle([15, 145, 245, 190], fill=(128, 128, 128))
    return image


def text_only_image() -> Image.Image:
    """The same readings with nothing continuous-tone anywhere on the frame."""
    image = Image.new("RGB", (WIDTH, HEIGHT), (255, 255, 255))
    _draw_readings(ImageDraw.Draw(image), ROWS, top=8)
    return image


def render(image: Image.Image, mode: DitherMode) -> Frame:
    """Run the real pipeline, so tone and sharpening are in play as they ship."""
    return process(
        image,
        PipelineOptions(
            width=WIDTH,
            height=HEIGHT,
            scheme=ColorScheme.MONO,
            dpi=124,
            dither=mode,
        ),
    )


@pytest.fixture(scope="module")
def dashboard() -> dict[DitherMode, Frame]:
    image = dashboard_image()
    return {mode: render(image, mode) for mode in (DitherMode.AUTO, DitherMode.NONE)}


@pytest.fixture(scope="module")
def text_only() -> dict[DitherMode, Frame]:
    image = text_only_image()
    modes = (DitherMode.AUTO, DitherMode.NONE, DitherMode.FLOYD_STEINBERG)
    return {mode: render(image, mode) for mode in modes}


def test_no_error_is_diffused_over_the_text(dashboard: dict[DitherMode, Frame]) -> None:
    """Over glyphs, AUTO must be indistinguishable from plain nearest-ink."""
    auto = dashboard[DitherMode.AUTO].indices[TEXT_BAND]
    nearest = dashboard[DitherMode.NONE].indices[TEXT_BAND]

    differing = int((auto != nearest).sum())

    assert differing == 0, f"{differing} pixel(s) of text were diffused rather than snapped"
    assert int((auto != auto[0, 0]).sum()) > 0, "the text band is blank; the fixture drew nothing"


def test_the_gradient_is_dithered_into_a_mixed_pattern(dashboard: dict[DitherMode, Frame]) -> None:
    """A broad field of intermediate tone is what error diffusion is *for*."""
    region = (GRADIENT_BAND, GRADIENT_COLUMNS)
    auto = dashboard[DitherMode.AUTO].indices[region]
    nearest = dashboard[DitherMode.NONE].indices[region]

    inks, counts = np.unique(auto, return_counts=True)
    assert len(inks) == 2, f"the gradient used {len(inks)} ink(s), so it was not dithered"
    assert counts.min() / counts.sum() > 0.2, "one ink swamped the gradient; that is a threshold"

    # A dithered gradient alternates inks along every row; a thresholded one
    # crosses over once, where the ramp passes the midpoint.
    def crossings(indices: np.ndarray) -> int:
        return int((np.diff(indices.astype(int), axis=1) != 0).sum())

    assert crossings(auto) > 10 * crossings(nearest), (
        "the gradient looks thresholded, not diffused"
    )


def test_text_alone_quantises_without_speckle(text_only: dict[DitherMode, Frame]) -> None:
    """The prototype's number: a page of text dithers to a speckle ratio of 0."""
    auto = text_only[DitherMode.AUTO]

    assert auto.metrics["speckle_ratio"] < 0.001
    assert np.array_equal(auto.indices, text_only[DitherMode.NONE].indices), (
        "AUTO diffused somewhere on a frame that has no continuous tone on it"
    )


def test_diffusing_everywhere_speckles_the_same_text(text_only: dict[DitherMode, Frame]) -> None:
    """Without the mask the grey labels break up — which is the finding."""
    auto = text_only[DitherMode.AUTO].metrics["speckle_ratio"]
    floyd_steinberg = text_only[DitherMode.FLOYD_STEINBERG].metrics["speckle_ratio"]

    assert floyd_steinberg > auto, (
        "Floyd-Steinberg over text should speckle where AUTO does not; the mask "
        "is not doing anything"
    )
    assert not np.array_equal(
        text_only[DitherMode.FLOYD_STEINBERG].indices, text_only[DitherMode.NONE].indices
    ), "Floyd-Steinberg changed nothing at all, so the comparison proves nothing"
