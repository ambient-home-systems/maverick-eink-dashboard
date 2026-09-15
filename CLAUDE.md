# CLAUDE.md

Maverick renders a Home Assistant dashboard in headless Chromium, restyles it
for ink, quantises it to a panel's measured palette, refuses blank or illegible
frames, and delivers the result over BLE, MQTT, HTTP pull, a webhook or a file.
It is a standalone service, configured by one YAML file and driven by a
scheduler, an HTTP API and Home Assistant state changes; the long version of
everything below is [CONTRIBUTING.md](CONTRIBUTING.md).

## Module map

| Package | Owns |
| --- | --- |
| `src/maverick/eink/` | Palettes, dithering, the image pipeline, the e-ink CSS theme, the lint gate, wire-format packing. No I/O. |
| `src/maverick/devices/` | The panel catalogue: `panels.yaml` and the `PanelProfile` that loads it. |
| `src/maverick/render/` | Chromium: browser pool, dashboard URL, injected theme, screenshot. |
| `src/maverick/transports/` | Getting a finished frame onto a panel, and the registry naming them. |
| `src/maverick/scheduling/` | When to render: intervals, cron, quiet hours, entity triggers, debouncing. |
| `src/maverick/server/` | The FastAPI app and the setup UI. |
| `src/maverick/ha/` | The Home Assistant REST/WebSocket client and MQTT discovery. |
| `src/maverick/esphome/` | Generating ESPHome device configuration for a display. |
| `engine.py`, `config.py` | The orchestrator and `FrameStore`; every pydantic model, and the source the reference is generated from. |

## Invariants — do not break these

- **Pipeline stage order** is `fit → rotate → tone → sharpen → greyscale →
  quantise → lint → pack` (`src/maverick/eink/pipeline.py:3-8`): resizing after
  quantisation destroys the dither, sharpening after it does nothing, rotating
  before fitting uses the wrong aspect ratio.
- **`TransportConfig` is `extra="allow"`** (`src/maverick/config.py:646`) while
  every other model, `DisplayConfig` included, inherits `Base` with
  `extra="forbid"` (`src/maverick/config.py:71-77`). A misspelt key must fail
  the load and name itself; transport options are the one exception, described
  by each transport's `options_doc` instead.
- **Frames are hashed on palette indices, not screenshots**
  (`src/maverick/eink/pipeline.py:89-97`). The digest covers what the panel will
  actually show, so sub-pixel noise upstream costs no e-ink refresh.
- **`FrameStore` stays persisted** (`src/maverick/engine.py:125-134`): a
  restart otherwise leaves a pull display with a `last_checksum` that suppresses
  re-rendering but no frame to serve, so a sleeping panel wakes to a 404.
- **A lint error blocks delivery unless `force`**
  (`src/maverick/engine.py:405-407`), because a bad frame persists on a battery
  panel for hours. `?force=true` and `--force` also override skip-unchanged.

## Documentation rules

- **Generated pages are never edited by hand.** All of `docs/reference/` bar
  the `_*.md` includes comes from `scripts/gen_docs.py`: edit the model, panel
  entry, `options_doc` or `_*.md` prose, run it, commit what it writes.
- **British spelling in prose** — colour, grey, quantise, catalogue. Identifiers
  and config keys keep what they shipped with: `color_scheme`, `gray4`.
- **No claim without a source file.** Every factual statement names the file it
  came from; if you cannot cite one, do not write the sentence.
- **The hardware-untested banner stays** on every page describing a device
  path; nothing here has been run on a panel. It comes off only when a
  contributor reports a successful run with the panel id and firmware version.

## Verifying a change

```bash
ruff check src tests
pytest -q
python scripts/gen_docs.py --check
python scripts/check_links.py     # relative Markdown links only, offline
```

## Looks like a bug, is deliberate

- **The frame endpoint sends no `Date` header**
  (`src/maverick/server/api.py:220-223`). The ASGI server emits one; a duplicate
  is invalid HTTP and e-ink firmware has exactly the minimal parsers that
  mishandle it. Devices read the clock from that header and need no SNTP.
- **`serpentine` is ignored on the fast dither path**
  (`src/maverick/eink/dither.py:286`). Pillow's Floyd–Steinberg is always
  left-to-right, so the option is dropped rather than silently promised.
- **Attribute-only state changes do not trigger a render**
  (`src/maverick/scheduling/scheduler.py:177-179`). Home Assistant fires
  `state_changed` for attribute updates too; a ticking timestamp attribute would
  otherwise refresh the panel continuously.
