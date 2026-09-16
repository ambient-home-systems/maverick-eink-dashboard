"""E-ink imaging: palettes, dithering, packing, styling and quality checks."""

from .dither import DitherMode, indices_to_image, quantize
from .lint import LintReport, LintThresholds, Severity, lint_frame
from .pack import FrameFormat, PackOptions, pack
from .palette import ColorScheme, Palette, get_palette
from .pipeline import FitMode, Frame, PipelineOptions, fit_to_panel, process
from .theme import ThemeOptions, TypeScale, build_css

__all__ = [
    "ColorScheme", "Palette", "get_palette",
    "DitherMode", "quantize", "indices_to_image",
    "FrameFormat", "PackOptions", "pack",
    "PipelineOptions", "Frame", "FitMode", "process", "fit_to_panel",
    "ThemeOptions", "TypeScale", "build_css",
    "LintReport", "LintThresholds", "Severity", "lint_frame",
]
