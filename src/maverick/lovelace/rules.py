"""The five rules for a dashboard on ink, in one screen.

The design guide (``docs/design-guide.md``) is eleven hundred lines, and
every one of them earns its place as a reference. It is also the wrong
length for the moment a person first points a display at a dashboard and
sees a grey smear. What they need then is the short version, where the
mistake is being made: in the setup UI, beside the starter and beside the
linter's findings. That is what this is.

Each rule is one sentence a person can act on, and one sentence saying why.
The *why* is the mechanism the design guide gives at length, so the guide's
own "five rules" section repeats these titles word for word and
``tests/test_starter_dashboard.py`` holds the two in step. Nothing here is
an opinion the guide does not already back with a table.
"""

from __future__ import annotations

from typing import Final

#: (title, what to do, why), in the order they matter.
RULES: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "Size text in millimetres",
        "Body text about 3 mm tall; nothing under 2.5 mm.",
        "Legibility depends on the size on the glass, not on pixels, and 124 dpi "
        "ink has no antialiasing to save small type.",
    ),
    (
        "No thin lines, no grey text",
        "Bold weights, solid black, borders at least a quarter of a millimetre.",
        "A two-ink panel has no grey: hairlines break up and grey text vanishes "
        "into dots.",
    ),
    (
        "Cards that are text on white",
        "Entities, markdown, glance, weather and to-do cards. No gauges, "
        "sparklines, photos or brightness sliders.",
        "Dark glyphs on a light ground snap to the nearest ink and stay crisp; "
        "tonal shapes turn to mush.",
    ),
    (
        "Colour is for alerts only",
        "On a panel with a red or yellow ink, use it to say 'look here' and "
        "nothing else.",
        "The colour ink refreshes slowly and ghosts; used as decoration it "
        "leaves nothing to signal with.",
    ),
    (
        "Count lines, not cards",
        "A panel holds a fixed number of lines of text and never scrolls.",
        "What does not fit is cut off, not moved to page two; the starter's "
        "line budget is the ceiling.",
    ),
)

__all__ = ["RULES"]
