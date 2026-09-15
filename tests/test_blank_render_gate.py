"""A frame that is almost entirely one ink must never reach a panel.

It is the cheapest check in the linter and the one that earns the most: a frame
that is 99.5 % a single ink is, in practice, a dashboard that did not load — an
expired token, a wrong URL, a card that threw. Pushed to a battery panel, that
blank frame sits there until the next wake, which can be a day.

So there are two halves to the claim, and both are tested here: `lint_frame`
raises `blank_render` at the threshold and not below it, and `Engine.render`
refuses to deliver a frame whose report has errors unless the caller forces it.
"""

from __future__ import annotations

import numpy as np

from maverick.eink.lint import LintThresholds, lint_frame
from maverick.eink.palette import ColorScheme, get_palette

PALETTE = get_palette(ColorScheme.MONO)
FRAME_SHAPE = (100, 100)


def frame_of(white_fraction: float) -> np.ndarray:
    """Palette indices for a frame that is ``white_fraction`` white ink."""
    total = FRAME_SHAPE[0] * FRAME_SHAPE[1]
    white = round(total * white_fraction)
    indices = np.full(total, PALETTE.black_index, dtype=np.uint8)
    indices[:white] = PALETTE.white_index
    return indices.reshape(FRAME_SHAPE)


def codes(report) -> list[str]:
    return [issue.code for issue in report.issues]


def test_a_frame_at_the_threshold_is_an_error() -> None:
    report = lint_frame(frame_of(0.995), PALETTE)

    assert "blank_render" in codes(report)
    assert report.ok is False
    assert [i.severity.value for i in report.errors] == ["error"]
    assert "99.50%" in report.errors[0].message


def test_a_frame_just_below_the_threshold_is_not() -> None:
    report = lint_frame(frame_of(0.994), PALETTE)

    assert "blank_render" not in codes(report)
    assert report.ok is True


def test_the_threshold_is_configurable_per_display() -> None:
    """Every threshold is a judgement call, so a panel may move this one."""
    lenient = LintThresholds(blank_ratio=0.999)

    assert lint_frame(frame_of(0.995), PALETTE, thresholds=lenient).ok is True
    assert lint_frame(frame_of(0.999), PALETTE, thresholds=lenient).ok is False


async def test_the_engine_skips_delivery_when_lint_fails(make_engine, blank_image) -> None:
    """The gate is what stops the engine handing a blank frame to a transport."""
    harness = await make_engine(blank_image)

    outcome = await harness.engine.render("kitchen", trigger="schedule")

    assert outcome.skipped is True
    assert outcome.reason.startswith("blocked by lint")
    assert outcome.frame is not None and "blank_render" in codes(outcome.frame.lint)
    assert harness.delivered == 0, "a blank frame was delivered"
    assert harness.state.skip_count == 1
    assert harness.state.last_checksum == "", "a frame that was never sent must not be recorded"


async def test_force_delivers_a_frame_the_gate_would_block(make_engine, blank_image) -> None:
    """`maverick render --force` and `?force=true` are the documented override."""
    harness = await make_engine(blank_image)

    outcome = await harness.engine.render("kitchen", trigger="manual", force=True)

    assert outcome.skipped is False
    assert outcome.ok is True
    assert harness.delivered == 1
    assert harness.state.skip_count == 0


async def test_the_gate_can_be_turned_off_for_the_whole_service(make_engine, blank_image) -> None:
    """`block_on_lint_error: false` is the other way past it, and it is global."""
    harness = await make_engine(blank_image)
    harness.engine.config.block_on_lint_error = False

    outcome = await harness.engine.render("kitchen", trigger="schedule")

    assert outcome.skipped is False
    assert harness.delivered == 1
    assert outcome.frame is not None and outcome.frame.lint.ok is False
