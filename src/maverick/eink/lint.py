"""Quality gate for rendered frames.

Enforcing e-ink style with CSS is only half the job: a custom card, a user
theme or a failed page load can still produce a frame that is unreadable on
ink. The linter inspects the *quantised* frame — what the panel will actually
show — and reports what a person would notice standing in front of it.

The most valuable check is the cheapest one: a frame that is 99.5% a single ink
almost always means the dashboard did not load (expired token, wrong URL, a
card that threw). Without this check that blank frame gets pushed to the panel
and sits there until the next refresh, and on a battery device that can be a
day. ``blank_render`` is an error by default and blocks delivery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from .palette import Palette


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class LintIssue:
    code: str
    severity: Severity
    message: str
    hint: str = ""


@dataclass
class LintReport:
    issues: list[LintIssue] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def errors(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, code: str, severity: Severity, message: str, hint: str = "") -> None:
        self.issues.append(LintIssue(code, severity, message, hint))

    def summary(self) -> str:
        if not self.issues:
            return "clean"
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.severity.value] = counts.get(issue.severity.value, 0) + 1
        return ", ".join(f"{n} {sev}" for sev, n in counts.items())


@dataclass
class LintThresholds:
    """Every threshold is a judgement call; all are overridable per display."""

    #: Fraction of the frame that may be a single ink before it looks blank.
    blank_ratio: float = 0.995
    #: Black coverage above this looks oppressive and slows BWR refreshes.
    max_ink_coverage: float = 0.62
    #: Fraction of inked pixels that may be 1-px hairlines.
    max_hairline_ratio: float = 0.28
    #: Fraction of the frame that may be isolated salt-and-pepper dither noise.
    max_speckle_ratio: float = 0.035
    #: Spot ink (red/yellow) coverage — it is slow and should be an accent.
    max_spot_coverage: float = 0.18
    #: Minimum legible stroke in millimetres at the panel's dpi.
    min_feature_mm: float = 0.18


def _neighbour_stack(mask: np.ndarray) -> np.ndarray:
    pad = np.pad(mask, 1, mode="edge")
    h, w = mask.shape
    return np.stack(
        [
            pad[dy : dy + h, dx : dx + w]
            for dy in range(3)
            for dx in range(3)
            if not (dx == 1 and dy == 1)
        ],
        axis=0,
    )


def _erode(mask: np.ndarray) -> np.ndarray:
    return _neighbour_stack(mask).all(axis=0) & mask


def lint_frame(
    indices: np.ndarray,
    palette: Palette,
    dpi: int = 124,
    thresholds: LintThresholds | None = None,
) -> LintReport:
    """Inspect a quantised frame and report e-ink legibility problems."""
    t = thresholds or LintThresholds()
    report = LintReport()
    total = int(indices.size)
    if total == 0:
        report.add("empty_frame", Severity.ERROR, "Frame has no pixels.")
        return report

    counts = np.bincount(indices.ravel(), minlength=len(palette))
    coverage = counts / total
    for name, frac in zip(palette.names, coverage, strict=False):
        report.metrics[f"coverage.{name}"] = round(float(frac), 5)

    # --- blank render ----------------------------------------------------
    dominant = int(coverage.argmax())
    if coverage[dominant] >= t.blank_ratio:
        report.add(
            "blank_render",
            Severity.ERROR,
            f"Frame is {coverage[dominant]:.2%} '{palette.names[dominant]}' — "
            "the dashboard almost certainly did not render.",
            "Check the access token, the dashboard URL and whether a card threw. "
            "Run `maverick render --debug` to keep the raw screenshot.",
        )

    # --- ink coverage ----------------------------------------------------
    ink = coverage.sum() - coverage[palette.white_index]
    report.metrics["coverage.ink"] = round(float(ink), 5)
    if ink > t.max_ink_coverage:
        report.add(
            "heavy_ink",
            Severity.WARNING,
            f"{ink:.1%} of the frame is inked.",
            "Dense frames take longer to refresh and ghost more. Prefer white "
            "space and rules over filled blocks.",
        )

    # --- spot ink usage --------------------------------------------------
    for spot in ("red", "yellow", "orange"):
        if spot in palette.names:
            frac = float(coverage[palette.index_of(spot)])
            if frac > t.max_spot_coverage:
                report.add(
                    f"spot_ink_overuse.{spot}",
                    Severity.WARNING,
                    f"{frac:.1%} of the frame uses the {spot} ink.",
                    "Spot inks refresh much more slowly than black. Reserve them "
                    "for alerts rather than decoration.",
                )

    # --- hairlines -------------------------------------------------------
    inked = indices != palette.white_index
    inked_count = int(inked.sum())
    if inked_count:
        survives = _erode(inked)
        hairline_ratio = 1.0 - (float(survives.sum()) / inked_count)
        report.metrics["hairline_ratio"] = round(hairline_ratio, 4)
        if hairline_ratio > t.max_hairline_ratio:
            report.add(
                "hairlines",
                Severity.WARNING,
                f"{hairline_ratio:.0%} of inked pixels are one pixel wide.",
                "Thin strokes break up on e-ink. Increase font weight, font size "
                "or border width — see docs/design-guide.md.",
            )

    # --- speckle ---------------------------------------------------------
    isolated = inked & ~_neighbour_stack(inked).any(axis=0)
    speckle_ratio = float(isolated.sum()) / total
    report.metrics["speckle_ratio"] = round(speckle_ratio, 5)
    if speckle_ratio > t.max_speckle_ratio:
        report.add(
            "dither_speckle",
            Severity.WARNING,
            f"{speckle_ratio:.2%} of the frame is isolated dither noise.",
            "Flat UI is being error-diffused. Use dither: auto (the default) or "
            "dither: none for dashboards without photographs.",
        )

    # --- physical feature size ------------------------------------------
    px_mm = 25.4 / dpi
    report.metrics["pixel_mm"] = round(px_mm, 4)
    if px_mm < t.min_feature_mm:
        report.add(
            "sub_threshold_pixel",
            Severity.INFO,
            f"One pixel is {px_mm:.3f} mm at {dpi} dpi.",
            "Single-pixel detail is below the eye's threshold at reading "
            "distance; rely on size and weight rather than hairline detail.",
        )

    # --- tonal range -----------------------------------------------------
    used = int((counts > 0).sum())
    report.metrics["inks_used"] = used
    if len(palette) > 4 and used <= 2:
        report.add(
            "palette_underused",
            Severity.INFO,
            f"Only {used} of {len(palette)} available inks were used.",
            "This panel can show more tone or colour than the render uses; a "
            "mono-styled theme may be masking its capability.",
        )

    return report


def lint_source(image_rgb: np.ndarray, quantised: np.ndarray, palette: Palette) -> dict[str, float]:
    """Measure how much the quantisation step actually cost.

    High mean error means the source used tones the panel cannot represent —
    usually a colourful theme on a monochrome panel.
    """
    pal = np.asarray(palette.colors, dtype=np.float32)
    rendered = pal[np.clip(quantised, 0, len(palette) - 1)]
    err = np.abs(image_rgb.astype(np.float32) - rendered).mean(axis=2)
    return {
        "quantisation_error.mean": round(float(err.mean()), 3),
        "quantisation_error.p95": round(float(np.percentile(err, 95)), 3),
    }


__all__ = ["lint_frame", "lint_source", "LintReport", "LintIssue", "LintThresholds", "Severity"]
