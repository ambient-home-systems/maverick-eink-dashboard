"""What the setup UI calls each setting, in plain words.

Every field of :class:`~maverick.config.DisplayConfig` carries a
``Field(description=...)``, and the reference page is generated from it
(``scripts/gen_docs.py``). Those descriptions are written for the reference:
they name the mechanism, the unit, the interaction with the next field over,
and the place in the source that decides it. Read as help text under a box in
a form, they are the reason the form felt like a manual. So the reference
keeps its prose, and this module gives the form its own: a short label a
person would say out loud, one sentence of help, and whether the setting is
one most people ever touch.

Three rules, held by ``tests/test_ui_copy.py``:

* **Every setting has an entry.** A field added to a model without copy here
  fails the suite rather than showing up in the drawer under its snake_case
  name with a paragraph beneath it.
* **Help is one short sentence.** Under 110 characters, no backticks, no
  cross-references. The reference description is still one click away in the
  form, under *More*.
* **Expert means hidden by default.** The Edit drawer shows the settings a
  person adjusting a panel actually changes — text size, brightness, when to
  refresh — and folds the rest behind one *Expert settings* switch. A section
  whose every setting is expert is hidden with them.

The setup UI reads this from ``GET /api/schema/display`` under ``ui``
(`src/maverick/server/api.py`) and falls back to the reference description for
anything missing, so an omission degrades to the old behaviour rather than to
a blank.
"""

from __future__ import annotations

from typing import Any, Final

#: The sections the Edit drawer draws, in order. ``expert`` sections are
#: hidden until the switch is on. ``panel`` is not a model of its own: it
#: gathers the six top-level geometry overrides out of *Display*, where a
#: person changing a name should not meet a dpi box.
SECTIONS: Final[dict[str, dict[str, Any]]] = {
    "": {"title": "Display", "help": ""},
    "pages": {"title": "Pages", "help": "Show more than one dashboard on this panel, in turn."},
    "schedule": {"title": "Schedule", "help": "When the panel refreshes."},
    "theme": {"title": "Look", "help": "How the page is restyled for ink."},
    "image": {"title": "Image", "help": "Brightness, contrast and dithering."},
    "transport": {"title": "Delivery", "help": "How the frame reaches the panel."},
    "panel": {
        "title": "Panel overrides",
        "help": "The panel supplies these. Change them only if it is wrong.",
        "expert": True,
    },
    "render": {"title": "Browser", "help": "How the page is captured.", "expert": True},
    "lint": {
        "title": "Checks",
        "help": "When a frame counts as blank or hard to read.",
        "expert": True,
    },
    "pack": {"title": "Controller quirks", "help": "Bit order for raw frames.", "expert": True},
    "esphome": {
        "title": "Board wiring",
        "help": "The board and pins written into the ESPHome file.",
        "expert": True,
    },
}


def _f(label: str, help: str, *, expert: bool = False, section: str | None = None) -> dict:
    entry: dict[str, Any] = {"label": label, "help": help, "expert": expert}
    if section is not None:
        entry["section"] = section
    return entry


#: Path -> copy. Paths are the field's dotted path in ``DisplayConfig``.
FIELDS: Final[dict[str, dict[str, Any]]] = {
    # ---- Display
    "id": _f("Id", "Fixed once saved. Used in web addresses and MQTT topics.", expert=True),
    "name": _f("Name", "Shown here and in Home Assistant."),
    "panel": _f("Panel", "The screen you have. It sets the size, the inks and how it is reached."),
    "dashboard": _f(
        "Dashboard",
        "The Home Assistant dashboard to show. Pick one, or paste a path.",
    ),
    "enabled": _f("Enabled", "Off keeps the display but stops rendering it."),
    "rotation": _f("Rotation", "Turn the image if the panel is mounted sideways."),
    "width": _f("Width (px)", "Leave empty to use the panel's own.", expert=True, section="panel"),
    "height": _f(
        "Height (px)",
        "Leave empty to use the panel's own.",
        expert=True, section="panel"
    ),
    "color_scheme": _f(
        "Inks", "Leave empty to use the panel's own.", expert=True, section="panel"
    ),
    "dpi": _f(
        "Pixels per inch",
        "Sets how big text is on the glass.",
        expert=True, section="panel"
    ),
    "frame_format": _f(
        "Frame format", "How the frame is packed for the device.", expert=True, section="panel"
    ),
    # ---- Pages
    "pages": _f("Pages", "Each page is a dashboard. The panel shows one at a time."),
    "rotate": _f("Rotate through pages", "Move to the next page automatically."),
    # ---- Schedule
    "schedule.every": _f("Refresh every", "For example 5m, 1h or 1d."),
    "schedule.quiet_hours": _f(
        "Quiet hours",
        "No refreshes between these times, e.g. 23:00-06:30.",
    ),
    "schedule.on_change": _f(
        "Refresh when these change", "Entity ids, comma separated."
    ),
    "schedule.full_refresh_every": _f(
        "Full refresh every",
        "Frames between flashing refreshes that clear ghosting. 0 uses the panel's default.",
    ),
    "schedule.enabled": _f("On the schedule", "Off renders only when asked.", expert=True),
    "schedule.cron": _f("Crontab", "Replaces Refresh every, e.g. 0 6-22 * * *.", expert=True),
    "schedule.debounce": _f("Debounce", "Ignore changes closer together than this.", expert=True),
    "schedule.skip_unchanged": _f(
        "Skip unchanged frames", "Do not send a frame identical to the last one.", expert=True
    ),
    "schedule.render_on_start": _f(
        "Render at startup", "Otherwise the first refresh waits one interval.", expert=True
    ),
    # ---- Look (theme)
    "theme.enabled": _f("Restyle for e-ink", "Off leaves Home Assistant's own styling."),
    "theme.body_mm": _f("Text size (mm)", "Height of normal text on the glass."),
    "theme.hide_chrome": _f("Hide toolbar and sidebar", "Shows only the dashboard itself."),
    "theme.use_spot_colour": _f(
        "Use the colour ink for alerts", "On panels with a red or yellow ink."
    ),
    "theme.extra_css": _f("Extra CSS", "Added last, so it overrides everything."),
    "theme.scale_ratio": _f("Heading scale", "How much larger each heading step is.", expert=True),
    "theme.min_font_weight": _f(
        "Minimum font weight", "Thinner strokes vanish on ink.", expert=True
    ),
    "theme.strong_font_weight": _f(
        "Heading font weight", "Used for headings and readings.", expert=True
    ),
    "theme.rule_mm": _f("Line thickness (mm)", "Thinner lines disappear.", expert=True),
    "theme.radius_mm": _f(
        "Corner radius (mm)",
        "Sharper corners dither more cleanly.",
        expert=True
    ),
    "theme.font_stack": _f(
        "Fonts",
        "CSS font list. Empty uses the built-in e-ink fonts.",
        expert=True
    ),
    "theme.letter_spacing_em": _f(
        "Letter spacing (em)", "Empty picks a value for the panel's dpi.", expert=True
    ),
    "theme.css_file": _f("CSS file", "Path to a stylesheet applied after Extra CSS.", expert=True),
    # ---- Image
    "image.dither": _f("Dithering", "How shades become dots. Auto suits most dashboards."),
    "image.fit": _f("Fit", "How the page is sized onto the panel."),
    "image.exposure": _f("Brightness", "1 is unchanged. Higher is lighter."),
    "image.contrast": _f("Contrast", "1 is unchanged."),
    "image.saturation": _f("Saturation", "Colour panels only. 1 is unchanged."),
    "image.sharpen": _f("Sharpen", "0 is off. A little restores edges ink softens."),
    "image.gamma": _f("Gamma", "Midtone correction before quantising.", expert=True),
    "image.serpentine": _f(
        "Serpentine dithering", "Alternate scan direction per row.", expert=True
    ),
    "image.black_level": _f(
        "Black level",
        "0 to 255. Darker than this becomes black.",
        expert=True
    ),
    "image.white_level": _f(
        "White level",
        "0 to 255. Lighter than this becomes white.",
        expert=True
    ),
    "image.invert": _f("Invert image", "For a panel that shows a negative.", expert=True),
    "image.palette_overrides": _f(
        "Measured ink colours", "The real colour of each ink, if you have measured it.", expert=True
    ),
    # ---- Delivery
    "transport.type": _f("Delivery method", "Automatic uses the panel's own."),
    # ---- Browser (render)
    "render.settle": _f("Settle time", "Extra wait after the page loads.", expert=True),
    "render.timeout": _f("Timeout", "Give up on a render after this long.", expert=True),
    "render.supersample": _f(
        "Supersample", "Render larger, then shrink. 2 sharpens text on coarse panels.", expert=True
    ),
    "render.viewport_width": _f(
        "Viewport width", "Browser width, if not the panel's.", expert=True
    ),
    "render.viewport_height": _f(
        "Viewport height", "Browser height, if not the panel's.", expert=True
    ),
    "render.zoom": _f("Zoom", "Zoom the page before capture.", expert=True),
    "render.wait_for_selector": _f(
        "Wait for element", "CSS selector that must appear before capture.", expert=True
    ),
    "render.crop_to_selector": _f(
        "Crop to element", "CSS selector to capture instead of the whole page.", expert=True
    ),
    "render.wait_for_images": _f(
        "Wait for images", "Capture only once every image has loaded.", expert=True
    ),
    "render.debug_artifacts": _f(
        "Keep debug files", "Save the raw screenshot and lint report to disk.", expert=True
    ),
    "render.keep_screenshot": _f(
        "Keep source screenshot", "Lets the card show the page before dithering.", expert=True
    ),
    # ---- Checks (lint)
    "lint.blank_ratio": _f(
        "Blank threshold", "Share of one ink that counts as a blank frame.", expert=True
    ),
    "lint.max_ink_coverage": _f(
        "Heavy ink threshold", "Share of dark ink that counts as too dense.", expert=True
    ),
    "lint.max_hairline_ratio": _f(
        "Hairline threshold", "Share of thin strokes that counts as too fine.", expert=True
    ),
    "lint.max_speckle_ratio": _f(
        "Speckle threshold", "Share of stray dots that counts as noisy.", expert=True
    ),
    "lint.max_spot_coverage": _f(
        "Colour ink threshold", "Share of a colour ink that counts as overuse.", expert=True
    ),
    "lint.min_feature_mm": _f(
        "Smallest visible detail (mm)", "Detail finer than this is flagged.", expert=True
    ),
    # ---- Controller quirks (pack)
    "pack.msb_first": _f("Most significant bit first", "Bit order within each byte.", expert=True),
    "pack.invert": _f("Invert bits", "For controllers where 1 means white.", expert=True),
    "pack.plane_order": _f("Plane order", "Ink order for the planes format.", expert=True),
    "pack.plane_active_low": _f(
        "Planes active low", "Invert each plane for controllers that expect it.", expert=True
    ),
    # ---- Board wiring (esphome)
    "esphome.board": _f("Board", "The PlatformIO board id, e.g. esp32dev.", expert=True),
    "esphome.clk_pin": _f("Clock pin", "SPI clock.", expert=True),
    "esphome.mosi_pin": _f("Data pin", "SPI data out.", expert=True),
    "esphome.cs_pin": _f("Chip select pin", "", expert=True),
    "esphome.dc_pin": _f("Data/command pin", "", expert=True),
    "esphome.busy_pin": _f("Busy pin", "", expert=True),
    "esphome.reset_pin": _f("Reset pin", "", expert=True),
    "esphome.deep_sleep": _f(
        "Battery mode", "Sleep between fetches. The board is unreachable while asleep.", expert=True
    ),
    "esphome.buffer_size": _f(
        "Download buffer (bytes)", "0 works it out from the panel.", expert=True
    ),
    "esphome.verify_ssl": _f(
        "Verify TLS certificate", "Off saves memory on the board.", expert=True
    ),
    "esphome.node_name": _f("Node name", "Empty derives one from the id.", expert=True),
}

#: Transport option -> one line of help, keyed by transport then option. The
#: label lives on the transport's ``OptionField``; this is the sentence under
#: the box. Options missing here fall back to ``options_doc``.
TRANSPORT_OPTIONS: Final[dict[str, dict[str, str]]] = {
    "opendisplay": {
        "mode": "Home Assistant reaches tags through its own Bluetooth and proxies.",
        "device_id": "Pick the tag. Its id from Home Assistant goes in the box.",
        "rotation": "The tag's own rotation, if it needs one.",
        "media_dir": "Where the frame is written for Home Assistant to pick up.",
        "media_root": "Home Assistant's media folder.",
        "media_source_prefix": "Rarely changed.",
        "mac": "The tag's Bluetooth address.",
        "device_name": "Its advertised name, if you do not know the address.",
        "encryption_key": "Only for tags that require one. 32 hex characters.",
        "timeout": "Seconds to wait for a connection.",
        "max_attempts": "Connection tries before giving up.",
        "scan_timeout": "Seconds a test scan listens for.",
    },
    "webhook": {
        "url": "Where to send the frame.",
        "method": "",
        "headers": "Extra headers, as JSON.",
        "timeout": "Seconds to wait for a reply.",
    },
    "file": {
        "path": "Folder the frame is written into.",
        "filename": "Empty names it after the display.",
        "write_preview": "Also save a viewable PNG.",
    },
    "mqtt": {
        "topic": "Empty uses the default topic for this display.",
    },
}


#: What to do about each lint finding, in one plain sentence. The linter's
#: own `hint` (`src/maverick/eink/lint.py`) names the mechanism; this names
#: the change, and the card shows it first. Keyed by the finding's code, or
#: its prefix for the per-ink `spot_ink_overuse.<ink>` findings.
LINT_ADVICE: Final[dict[str, str]] = {
    "empty_frame": (
        "Nothing rendered at all. Check the dashboard path and the Home Assistant link."
    ),
    "blank_render": (
        "The page did not load. Check the dashboard path, then the Home Assistant link."
    ),
    "heavy_ink": "Too much dark ink. Use white space and lines instead of filled blocks.",
    "spot_ink_overuse": (
        "Too much colour ink. Keep the colour for alerts and let the rest be black."
    ),
    "hairlines": "Lines and letters are too thin. Use bolder text and thicker borders.",
    "dither_speckle": (
        "Flat areas are turning to dots. Set Dithering to auto, or none for text-only pages."
    ),
    "sub_threshold_pixel": (
        "Fine detail is smaller than the eye can see on this panel. Use size and weight instead."
    ),
    "palette_underused": (
        "This panel can show more shades than the page uses. Turn Restyle for e-ink off to check."
    ),
}


def lint_advice(code: str) -> str:
    """The plain sentence for a finding, matching `spot_ink_overuse.red` by prefix."""
    return LINT_ADVICE.get(code) or LINT_ADVICE.get(code.split(".")[0], "")


def ui_copy() -> dict[str, Any]:
    """The whole thing, as ``GET /api/schema/display`` serves it."""
    return {
        "sections": SECTIONS,
        "fields": FIELDS,
        "transports": TRANSPORT_OPTIONS,
    }


__all__ = ["FIELDS", "LINT_ADVICE", "SECTIONS", "TRANSPORT_OPTIONS", "lint_advice", "ui_copy"]
