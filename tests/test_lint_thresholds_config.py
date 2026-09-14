"""`displays[].lint` must reach the linter.

`LintThresholds` says "every threshold is a judgement call; all are overridable
per display", and `PipelineOptions` has carried a `lint_thresholds` field all
along — but nothing populated it and no config key mapped to it, so every
display linted against the defaults and the docstring was aspirational. These
tests pin the wiring from the YAML key to the report.
"""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from maverick.config import Config
from maverick.eink.lint import LintThresholds, lint_frame
from maverick.eink.palette import ColorScheme, get_palette
from maverick.engine import Engine


def _config(lint: dict[str, float] | None, tmp_path) -> Config:
    display: dict[str, object] = {"id": "kitchen", "panel": "waveshare-7in5-mono"}
    if lint is not None:
        display["lint"] = lint
    return Config.model_validate({"data_dir": str(tmp_path), "displays": [display]})


def test_defaults_match_the_dataclass(tmp_path) -> None:
    """An unconfigured display must lint exactly as it did before the key existed."""
    config = _config(None, tmp_path)

    options = Engine(config)._pipeline_options(config.display("kitchen").resolved())

    assert options.lint_thresholds == LintThresholds()


def test_overrides_reach_the_pipeline(tmp_path) -> None:
    config = _config({"max_ink_coverage": 0.4, "max_spot_coverage": 0.05}, tmp_path)

    options = Engine(config)._pipeline_options(config.display("kitchen").resolved())

    assert options.lint_thresholds.max_ink_coverage == 0.4
    assert options.lint_thresholds.max_spot_coverage == 0.05
    # Everything not named keeps its default rather than being zeroed.
    assert options.lint_thresholds.blank_ratio == LintThresholds().blank_ratio


def test_a_lowered_threshold_changes_the_report() -> None:
    """The wiring is only worth having if the number actually moves the finding."""
    palette = get_palette(ColorScheme.MONO)
    frame = np.full((100, 100), palette.white_index, dtype=np.uint8)
    frame[:50] = palette.black_index  # exactly half the frame inked

    assert "heavy_ink" not in [i.code for i in lint_frame(frame, palette).issues]

    strict = lint_frame(frame, palette, thresholds=LintThresholds(max_ink_coverage=0.4))
    assert "heavy_ink" in [i.code for i in strict.issues]


@pytest.mark.parametrize(
    "bad",
    [
        {"blank_ratio": 1.5},
        {"max_ink_coverage": -0.1},
        {"min_feature_mm": 0.0},
    ],
)
def test_out_of_range_thresholds_fail_at_load(bad: dict[str, float], tmp_path) -> None:
    """A fraction outside 0-1 is a typo, and should fail before the first render."""
    with pytest.raises(ValidationError):
        _config(bad, tmp_path)
