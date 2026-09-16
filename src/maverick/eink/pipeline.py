"""The image pipeline: screenshot in, panel-ready frame out.

Order matters here. Resizing after quantisation destroys the dither pattern;
sharpening after quantisation does nothing; rotating before fitting uses the
wrong aspect ratio. The sequence below is the one that survives contact with
real panels:

    fit → rotate → tone → sharpen → greyscale → quantise → lint → pack
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .dither import DitherMode, indices_to_image, quantize
from .lint import LintReport, LintThresholds, lint_frame, lint_source
from .pack import FrameFormat, PackOptions, pack
from .palette import ColorScheme, Palette, get_palette


class FitMode(str, Enum):
    CONTAIN = "contain"
    COVER = "cover"
    STRETCH = "stretch"
    CROP = "crop"


@dataclass
class PipelineOptions:
    """Image treatment for one display."""

    width: int
    height: int
    scheme: ColorScheme = ColorScheme.MONO
    dpi: int = 124
    rotation: int = 0
    fit: FitMode = FitMode.CONTAIN
    dither: DitherMode = DitherMode.AUTO
    serpentine: bool = True

    #: Multiplies luminance before quantisation. >1 lightens.
    exposure: float = 1.0
    #: Contrast stretch. E-ink benefits from a little more than a screen wants.
    contrast: float = 1.08
    #: Gamma applied before quantisation. Panels are closer to linear than sRGB,
    #: so a mild decode keeps midtones from crushing to black.
    gamma: float = 1.0
    #: Saturation boost for colour panels. Their gamut is small; pushing
    #: saturation first means more pixels land on a real ink instead of
    #: dithering between two.
    saturation: float = 1.0
    #: Unsharp mask radius in pixels. Ink bleeds slightly; a light sharpen
    #: restores the edge the panel loses. 0 disables.
    sharpen: float = 0.6
    #: Clip points, 0-255, applied as a level stretch.
    black_level: int = 0
    white_level: int = 255
    invert: bool = False

    frame_format: FrameFormat = FrameFormat.PNG
    pack_options: PackOptions = field(default_factory=PackOptions)
    palette_overrides: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    lint_thresholds: LintThresholds = field(default_factory=LintThresholds)

    def palette(self) -> Palette:
        return get_palette(self.scheme, self.palette_overrides or None)


@dataclass
class Frame:
    """The result of processing one screenshot for one display."""

    indices: np.ndarray
    palette: Palette
    width: int
    height: int
    preview: Image.Image
    payload: bytes
    frame_format: FrameFormat
    lint: LintReport
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def checksum(self) -> str:
        """Stable digest of the *panel-visible* content.

        Hashing the payload rather than the screenshot means a re-render that
        differs only in sub-pixel noise does not trigger a refresh — which on a
        battery panel is the difference between weeks and days of runtime.
        """
        return hashlib.sha256(self.indices.tobytes()).hexdigest()[:16]

    @property
    def etag(self) -> str:
        return f'"{self.checksum}"'


def _fit(image: Image.Image, width: int, height: int, mode: FitMode) -> Image.Image:
    """Resize onto the panel, padding with white rather than black."""
    white = (255, 255, 255)
    if mode is FitMode.STRETCH:
        return image.resize((width, height), Image.LANCZOS)
    if mode is FitMode.COVER:
        return ImageOps.fit(image, (width, height), method=Image.LANCZOS, centering=(0.5, 0.0))
    if mode is FitMode.CROP:
        canvas = Image.new("RGB", (width, height), white)
        canvas.paste(image, (0, 0))
        return canvas
    # CONTAIN
    scaled = image.copy()
    scaled.thumbnail((width, height), Image.LANCZOS)
    canvas = Image.new("RGB", (width, height), white)
    canvas.paste(scaled, ((width - scaled.width) // 2, (height - scaled.height) // 2))
    return canvas


def _tone(image: Image.Image, opts: PipelineOptions) -> Image.Image:
    if opts.black_level > 0 or opts.white_level < 255:
        lo, hi = opts.black_level, max(opts.black_level + 1, opts.white_level)
        lut = np.clip((np.arange(256) - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
        image = image.point(list(lut) * len(image.getbands()))
    if opts.gamma != 1.0:
        lut = np.clip(((np.arange(256) / 255.0) ** (1.0 / opts.gamma)) * 255.0, 0, 255)
        image = image.point(list(lut.astype(np.uint8)) * len(image.getbands()))
    if opts.exposure != 1.0:
        image = ImageEnhance.Brightness(image).enhance(opts.exposure)
    if opts.contrast != 1.0:
        image = ImageEnhance.Contrast(image).enhance(opts.contrast)
    if opts.saturation != 1.0:
        image = ImageEnhance.Color(image).enhance(opts.saturation)
    return image


def fit_to_panel(
    image: Image.Image, width: int, height: int, rotation: int, fit: FitMode
) -> Image.Image:
    """Fit, rotate and resize onto the panel's final geometry — no tone or quantisation.

    The first stage of :func:`process` (`fit → rotate → …`), factored out so
    `Engine`'s kept screenshot (`render.keep_screenshot`,
    `src/maverick/config.py`) gets the same geometry the quantised frame will,
    without duplicating the maths.
    """
    # The panel's logical resolution before rotation: a 90/270 rotation means we
    # fit the screenshot to the transposed size.
    if rotation in (90, 270):
        fit_w, fit_h = height, width
    else:
        fit_w, fit_h = width, height

    rgb = _fit(image.convert("RGB"), fit_w, fit_h, fit)
    if rotation:
        rgb = rgb.rotate(-rotation % 360, expand=True, fillcolor=(255, 255, 255))
    if (rgb.width, rgb.height) != (width, height):
        rgb = rgb.resize((width, height), Image.LANCZOS)
    return rgb


def process(image: Image.Image, opts: PipelineOptions) -> Frame:
    """Run a screenshot through the full pipeline."""
    palette = opts.palette()
    rgb = fit_to_panel(image, opts.width, opts.height, opts.rotation, opts.fit)
    rgb = _tone(rgb, opts)

    if opts.sharpen > 0:
        rgb = rgb.filter(
            ImageFilter.UnsharpMask(radius=opts.sharpen, percent=110, threshold=3)
        )
    if opts.scheme.is_greyscale:
        rgb = rgb.convert("L").convert("RGB")
    if opts.invert:
        rgb = ImageOps.invert(rgb)

    source = np.asarray(rgb, dtype=np.uint8)
    indices = quantize(rgb, palette, opts.dither, opts.serpentine)

    report = lint_frame(indices, palette, opts.dpi, opts.lint_thresholds)
    metrics = dict(report.metrics)
    metrics.update(lint_source(source, indices, palette))

    preview = indices_to_image(indices, palette)
    payload = _encode(indices, preview, palette, opts)

    return Frame(
        indices=indices,
        palette=palette,
        width=opts.width,
        height=opts.height,
        preview=preview,
        payload=payload,
        frame_format=opts.frame_format,
        lint=report,
        metrics=metrics,
    )


def _encode(
    indices: np.ndarray,
    preview: Image.Image,
    palette: Palette,
    opts: PipelineOptions,
) -> bytes:
    fmt = opts.frame_format
    if fmt in (FrameFormat.PNG, FrameFormat.BMP):
        buffer = io.BytesIO()
        # Save as a palette image: far smaller than RGB and it preserves the
        # exact inks, so nothing re-quantises the frame downstream.
        out = Image.fromarray(indices, mode="P")
        flat = palette.flat()
        out.putpalette(flat + [0] * (768 - len(flat)))
        if fmt is FrameFormat.BMP and len(palette) <= 2:
            out = out.convert("1")
        out.save(buffer, format=fmt.value.upper())
        return buffer.getvalue()
    return pack(indices, palette, fmt, opts.pack_options)


__all__ = ["PipelineOptions", "Frame", "FitMode", "process", "fit_to_panel"]
