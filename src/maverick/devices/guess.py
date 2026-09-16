"""Guess a catalogue panel from what a device says about itself.

Home Assistant's device registry carries a `model` and a `manufacturer`
string for every OpenDisplay tag the integration has found, and a BLE scan
carries an advertised name. None of those is a Maverick panel id, but all of
them tend to carry the size of the glass (``2.9``, ``4.2"``, ``7.5in``) and
sometimes its inks (``BWR``, ``red``, ``mono``, ``Spectra``). This turns that
into the closest catalogue id, so the setup UI's *Add as display* can
pre-select the panel and *Test delivery* can be the first thing the user
does rather than the fifth.

It is a guess and the UI says so. A wrong size is caught the moment a frame
is rendered — the preview is the wrong shape — and the panel field stays a
picker the user can change. What this has to get right is the common case:
a device that names its size at all.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .profiles import PanelProfile, all_panels

#: A size in inches as devices write it: `2.9`, `2.9"`, `2in9`, `7.5in`, `4.26`.
_SIZE = re.compile(r"(?<![\d.])(\d{1,2})(?:[.,]|in)(\d{1,2})(?![\d])", re.IGNORECASE)

#: Words that name an ink set, and the catalogue schemes they point at.
_INK_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("spectra", ("spectra6",)),
    ("e6", ("spectra6",)),
    ("6c", ("spectra6",)),
    ("7c", ("acep7",)),
    ("acep", ("acep7",)),
    ("bwry", ("bwry",)),
    ("bwr", ("bwr",)),
    ("red", ("bwr", "bwry")),
    ("bwy", ("bwy",)),
    ("yellow", ("bwy", "bwry")),
    ("mono", ("mono",)),
    ("bw", ("mono",)),
)

#: Words that name a family of tag, and the vendor or id fragment to prefer.
_FAMILY_HINTS: tuple[tuple[str, str], ...] = (
    ("flex", "flex"),
    # Solum's own model numbers (ST-GR29000 and so on), which the integration
    # reports as the model when the vendor firmware left them readable.
    ("st-gr", "solum"),
    ("xiao", "xiao"),
    ("seeed", "xiao"),
    ("solum", "solum"),
    ("waveshare", "waveshare"),
    ("inky", "inky"),
    ("trmnl", "trmnl"),
    ("kindle", "kindle"),
    ("kobo", "kobo"),
)


def _sizes(text: str) -> set[str]:
    """Every size in `text` as the catalogue writes it in an id: `2in9`, `4in26`."""
    found = set()
    for whole, fraction in _SIZE.findall(text):
        found.add(f"{int(whole)}in{fraction.rstrip('0') or '0'}")
    return found


def _panel_size(panel: PanelProfile) -> str | None:
    match = re.search(r"(\d{1,2})in(\d{1,2})", panel.id)
    if not match:
        return None
    return f"{int(match.group(1))}in{match.group(2).rstrip('0') or '0'}"


def guess_panel(*texts: str | None, prefer_transport: str | None = None) -> str | None:
    """The catalogue id that best matches the words in `texts`, or None.

    `prefer_transport` narrows the field to panels whose `default_transport`
    is that one — `opendisplay` for a device Home Assistant's OpenDisplay
    integration reported, which rules out a Waveshare module of the same
    size that is reached over HTTP. A panel scores for a matching size, for
    matching inks and for a matching family; with no size in the text there
    is no guess, because inks alone would pick a panel at random.
    """
    text = " ".join(t for t in texts if t).lower()
    if not text:
        return None
    sizes = _sizes(text)
    if not sizes:
        return None
    best: tuple[int, str] | None = None
    for panel in _candidates(prefer_transport):
        size = _panel_size(panel)
        if size is None or size not in sizes:
            continue
        score = 10
        for word, schemes in _INK_HINTS:
            if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text):
                score += 4 if panel.color_scheme.value in schemes else -3
                break
        for word, fragment in _FAMILY_HINTS:
            if word in text:
                score += 3 if fragment in panel.id or fragment in panel.vendor else -1
        if best is None or score > best[0]:
            best = (score, panel.id)
    return best[1] if best else None


def _candidates(prefer_transport: str | None) -> Iterable[PanelProfile]:
    panels = all_panels()
    if prefer_transport:
        narrowed = [p for p in panels if p.default_transport == prefer_transport]
        if narrowed:
            return narrowed
    return panels


__all__ = ["guess_panel"]
