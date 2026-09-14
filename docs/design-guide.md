# Design guide

The full guide — how to lay out a dashboard that stays legible on e-ink — is
still being written. In the meantime, the lint thresholds below describe the
constraints a dashboard needs to stay within.

- **`blank_ratio` (0.995)** — fraction of the frame that may be a single ink
  before it looks blank.
- **`max_ink_coverage` (0.62)** — black coverage above this looks oppressive
  and slows BWR refreshes.
- **`max_hairline_ratio` (0.28)** — fraction of inked pixels that may be
  1-px hairlines.
- **`max_speckle_ratio` (0.035)** — fraction of the frame that may be
  isolated salt-and-pepper dither noise.
- **`max_spot_coverage` (0.18)** — spot ink (red/yellow) coverage; it is slow
  and should be an accent.
- **`min_feature_mm` (0.18)** — minimum legible stroke in millimetres at the
  panel's dpi.
