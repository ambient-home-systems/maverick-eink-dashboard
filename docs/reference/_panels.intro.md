Maverick ships a catalog of physical panels so a display config can say
`panel: waveshare-7in5-mono` and be done with the hardware. This page lists
every entry in `src/maverick/devices/panels.yaml`, grouped by vendor the same
way `maverick panels` prints them.

## What the columns mean

| Column | Meaning | Where it is used |
| --- | --- | --- |
| `id` | The value a display's `panel:` key names. | `get_panel` in `devices/profiles.py`. |
| Name | The panel's marketable name. | Printed by `maverick panels` and `maverick check`. |
| Resolution | Native width × height, in pixels, before rotation. | `DisplayConfig.resolved()` falls back to it when a display sets no `width`/`height`. |
| Color scheme | One of the [`ColorScheme`](configuration.md#color-schemes) values. | Picks the quantization palette and, through `has_spot_colour`, the linter's spot-ink checks. |
| dpi | Approximate pixel density, from the datasheet. | Drives the millimeter-to-pixel type scale in `eink/theme.py` (`mm_to_px`, `TypeScale.px`) and the hairline-width check in `eink/lint.py` (`25.4 / dpi`). A wrong dpi makes body text the wrong physical size on the panel, not just the wrong pixel size. |
| Partial refresh | Whether the controller can update part of the panel without a full flash. | `Engine._needs_full_refresh` forces a full refresh on every render when this is `no`. |
| Full-refresh cadence | How often a full (flashing) refresh is forced to clear ghosting, from `full_refresh_every`. | Same method; a display's `schedule.full_refresh_every` overrides it. |
| Default transport | The transport a display uses if it sets none of its own. | `DisplayConfig.resolved()`, via `default_transport`. |
| ESPHome model | The `online_image`/display component model string for `maverick esphome`. | `esphome/generator.py`. Panels with no ESPHome path (BLE tags, e-readers) leave this blank. |
| Notes | Free text: caveats, refresh speed, what the entry has actually been checked against. | Datasheet-only entries say nothing here; an entry that has been run on real hardware says so. |

`native_rotation` and `default_format` are not shown as their own columns —
they feed `DisplayConfig.resolved()` the same way `dpi` does, supplying the
panel's own rotation and wire format whenever a display does not override
them, but they rarely need to be read off this page on their own.

## The generic fallback

`generic-mono` is the panel to reach for when your hardware is not listed:
800×480 monochrome at 124 dpi, no partial refresh. Override its resolution
per display:

```yaml
displays:
  - id: mystery-panel
    panel: generic-mono
    width: 640
    height: 384
    dashboard: /lovelace-eink/mystery
```

Any panel profile can be overridden the same way — `width`, `height`,
`color_scheme`, `dpi`, `rotation` and `frame_format` are all per-display keys
that win over the catalog entry (see
[Panel profiles and per-display overrides](configuration.md#panel-profiles-and-per-display-overrides)).

## Adding a panel

Add an entry to `src/maverick/devices/panels.yaml` with the fields
`PanelProfile` (`src/maverick/devices/profiles.py`) declares:

- `id`, `name`, `vendor` — identity.
- `width`, `height` — native resolution in pixels.
- `color_scheme` — one of `mono`, `bwr`, `bwy`, `bwry`, `gray4`, `gray8`,
  `gray16`, `spectra6`, `acep7`.
- `dpi` — from the datasheet; defaults to 124 if you omit it, which is almost
  certainly wrong for your panel.
- `native_rotation` — 0, 90, 180 or 270; how the panel is wired versus how the
  image should be drawn.
- `default_transport` — the transport name a display gets if it names none.
- `default_format` — a `FrameFormat` value, if the panel needs something other
  than the transport's own default.
- `measured_palette` — a note of which measured-ink constant applies, if you
  have one.
- `esphome_model`, `supports_partial`, `full_refresh_every` — as described
  above.
- `notes` — say what you actually verified.

No code change is required: `all_panels()` reads the YAML at import time, so
a new entry appears in `maverick panels` and this page as soon as the file is
regenerated.

Every value in this catalog is transcribed from a datasheet or a vendor
product page, not measured on a physical unit, unless an entry's notes say
otherwise.
