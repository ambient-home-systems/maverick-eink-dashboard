# Maverick — Architecture

> Last reviewed against commit `609b882`.
>
> This page describes the service as it is built. Everything proposed but not
> written — the add-on, the integration, pages, the authoring tools — lives in
> [roadmap.md](roadmap.md).

Maverick loads a Home Assistant dashboard in headless Chromium, restyles it for
ink, quantises it against a panel's measured pigments, refuses to ship a frame
that is blank or illegible, and hands the result to a transport. It runs as a
standalone Python service: a CLI, an HTTP API, a scheduler, and MQTT discovery
so the panels appear in Home Assistant as devices.

**The rendering is the easy part.** Everything interesting in the code is
downstream of the screenshot: whether the output is legible on ink, and whether
a battery panel survives more than a week. Both of those shape the design more
than the browser does.

## The pipeline as implemented

Four movements. Each owns a file, and each can be run without the ones after it
— `maverick render --no-deliver -o frame.png` stops after the third.

| Stage | Owner | What it does |
|---|---|---|
| Render | `src/maverick/render/dashboard.py`, `render/browser.py` | Headless Chromium at panel geometry, auth seeded pre-navigation, the e-ink stylesheet adopted into every shadow root |
| Process | `src/maverick/eink/pipeline.py` (`process`) | Eight steps, in an order that matters — below |
| Gate | `src/maverick/eink/lint.py`, `engine.py` (`Engine.render`) | A frame that fails a hard check is not worth an e-ink refresh |
| Deliver | `src/maverick/transports/` | BLE, MQTT, HTTP pull, a webhook or a file |

`Engine.render` in `src/maverick/engine.py` is the seam that joins them, and it
is serialised per display: a manual refresh arriving mid-render would otherwise
have two writers on one panel.

### Process, in order

The order is the whole point of the module, and its docstring says so: *fit →
rotate → tone → sharpen → greyscale → quantise → lint → pack*. Resizing after
quantisation destroys the dither pattern; sharpening after it does nothing at
all; rotating before fitting uses the wrong aspect ratio.

| # | Step | Why it sits here |
|---|---|---|
| 1 | Fit | Contain, cover, stretch or crop onto the panel. Padding is white, because white is the ground on e-ink and black padding reads as a broken frame |
| 2 | Rotate | The browser rendered a landscape viewport for a panel mounted in portrait; the rotation happens once the image is at panel geometry |
| 3 | Tone | Levels, gamma, exposure, contrast, saturation. After quantisation there are only inks left to adjust |
| 4 | Sharpen | An unsharp mask restores the edge ink bleed takes away. Defaults to a radius of 0.6 px |
| 5 | Greyscale | For mono and grey schemes, so quantisation is not fed colour it cannot use |
| 6 | Quantise | Content-aware dithering against measured pigments (`eink/dither.py`, `eink/palette.py`) |
| 7 | Lint | Inspects the *quantised* indices — what the panel will actually show, not the screenshot |
| 8 | Pack | `packed` (1, 2, 4 or 8 bits per pixel, from the palette size), `planes` (one 1-bit plane per ink) or `indexed`; PNG and BMP are written by Pillow as palette images so nothing re-quantises downstream |

A frame's identity is the digest of its palette indices, not of the screenshot
(`Frame.checksum`). A re-render that differs only in sub-pixel noise therefore
produces the same checksum — which is what makes the skip-unchanged path below
worth anything. The ETag served to devices is that checksum, quoted.

**One lint check has teeth.** `blank_render` fires when a single ink covers
99.5 % of the frame (`LintThresholds.blank_ratio`), because in practice that
means the dashboard did not load — an expired token, a wrong URL, a card that
threw. It is an error rather than a warning, so with `block_on_lint_error` set
`Engine.render` counts a skip and leaves the panel showing the last good frame
rather than spending a refresh on a blank one. `force` overrides it for one
render, and `block_on_lint_error: false` for good.

*Verified:
[`tests/test_blank_render_gate.py`](../tests/test_blank_render_gate.py) —
`test_a_frame_at_the_threshold_is_an_error` and
`test_a_frame_just_below_the_threshold_is_not` fix the ratio;
`test_the_engine_skips_delivery_when_lint_fails` and
`test_force_delivers_a_frame_the_gate_would_block` drive `Engine.render` with a
stub renderer and a stub transport.*

## Rendering

**One Chromium, one browser context per display, a fresh page per render.** The
context is what holds the Home Assistant session in `localStorage`, so
rebuilding it per render would mean a full frontend bootstrap every time —
several seconds on a Raspberry Pi. Contexts are keyed by everything fixed at
creation time (viewport, device scale factor, a hash of the init scripts), so a
display whose geometry or theme changes transparently gets a new one.

**Concurrency is capped by a semaphore**, `MAVERICK_MAX_RENDERS`, default 2
(`engine.py`). This is not a throughput knob but a memory one: two 1872×1404
dashboards rendered at supersample 2 at the same time are a ~250 MB spike, which
is enough to get the process OOM-killed on small hardware.

**The auth bundle is installed by an init script.** The frontend ignores an
`Authorization` header — it reads a token bundle from `localStorage.hassTokens`.
`build_auth_bundle` assembles one from the configured long-lived token with ten
years of validity, and the script runs *before the page's first line of
JavaScript*, so there is never a navigation that has already bounced to the
login screen. The same script also forces the light theme and hides the sidebar
and the onboarding dialogs, any of which would otherwise sit on top of the
dashboard.

**The stylesheet is adopted, not appended.** The frontend is web components
throughout, so a stylesheet on `document.head` reaches almost nothing. The
injected script builds one `CSSStyleSheet` and adopts it into the document and
every shadow root, then patches `Element.prototype.attachShadow` so roots
created later by lazily-rendering cards get it too. The renderer re-walks the
tree twice more — once the dashboard's root element appears, and again after the
settle delay — to catch whatever the lazily-rendering cards attached in between.

**The login page is detected, not screenshotted.** After navigation the renderer
checks the landed URL for `/auth/authorize` or a `/lovelace/login` ending, and
the DOM for `ha-authorize` or `ha-auth-flow`. Either one drops the cached
context and raises `RenderError` with the reason — a missing, expired or
wrong-origin token. Failing loudly
matters because the alternative is a login screen sitting on a battery panel
until its next wake.

**`networkidle` has a fallback.** It is the right wait for a dashboard that
settles, and it never settles on one with a live camera or a streaming graph, so
a timeout retries with `domcontentloaded` rather than failing the render.

**Supersampling is on by default.** Rendering at 2× panel resolution and letting
the fit step downsample gives markedly better text on low-dpi panels; it costs
memory and time, which is why it interacts with the concurrency cap above.

Type size, by contrast, is not a rendering trick: `eink/theme.py` sizes type in
millimetres and converts with the panel's dpi, carrying the result as a single
root `zoom`. [The design guide](design-guide.md) covers that half.

## State that survives a restart

Two pieces of per-display state earn persistence to `<data_dir>/state.json`; the
rest of `DisplayState` is diagnostics that ride along with them.

| Field | Why it is kept |
|---|---|
| `last_checksum` | Lets a scheduled render that produced an identical frame skip delivery — the single biggest lever on battery runtime |
| `frames_since_full` | Ghosting accumulates across partial refreshes. Counting across restarts means a service that restarts often does not quietly stop clearing ghosts |
| `sequence`, `render_count`, `skip_count` | Counters published on the MQTT state topic; `sequence` is also the *Frames delivered* sensor |
| `last_render_at`, `last_delivery_at`, `last_pulled_at` | Enough to see that a panel has silently stopped updating |
| `last_error`, `consecutive_failures` | Drives the `status` sensor and the problem binary sensor |

**Frames are persisted too, and that is a correctness fix rather than a cache.**
`FrameStore` writes each frame, its preview PNG and its metadata under
`<data_dir>/frames/`. Without it, a restart leaves a pull-transport display with
a `last_checksum` that suppresses re-rendering but no frame to serve, so a
sleeping panel wakes to a 404 and keeps showing a stale screen until the
dashboard content happens to change. Restoring from disk also means a device
that wakes seconds after a restart is served immediately instead of waiting for
the next scheduled render.

Both the state file and the frame files are written write-then-rename, so a
device fetching mid-write never sees a truncated frame and a crash mid-save
never leaves unparseable JSON. An unreadable file is logged and ignored, never
fatal.

## Push, pull, and "pending"

The split between the two kinds of transport matters more than it looks.

**Push** transports — MQTT, OpenDisplay BLE, the webhook, the file writer —
reach out to something that is listening. Delivery succeeds or fails now, and
the caller learns which.

**Pull** transports cannot deliver anything. A battery ESP32 sleeps for ten
minutes, wakes, fetches, and sleeps again; the transport's job is to publish the
frame and let the device collect it. Conflating the two produces a scheduler
that believes it has updated a panel that is still asleep, so
`DeliveryResult.pending` exists to say *published, not yet collected*
(`transports/base.py`). `Transport.pushes` is the class-level flag that says
which kind a transport is.

**The ETag contract is the battery budget.** `/api/displays/{id}/frame` serves
the frame with a strong ETag — the frame checksum — and returns 304 to a device
whose `If-None-Match` matches. The saving is not the download; it is the e-ink
refresh that does not happen. Alongside it the response carries
`X-Maverick-Next-Refresh`, so a device knows how long it may deep-sleep, plus
the checksum, geometry and ink count as headers. The endpoint deliberately adds
no `Date` header of its own: the ASGI server already emits one, a duplicate is
invalid HTTP, and minimal firmware parsers are exactly the clients that
mishandle it.

**Skip-unchanged has a guard.** When a frame's checksum equals `last_checksum`
and `schedule.skip_unchanged` is set, `Engine.render` skips delivery — unless
the frame is not *servable*. A pull transport can only skip if a frame is
genuinely available to serve, so the check is `transport.pushes or display_id in
self.frames`: with an empty frame store the frame is delivered anyway, rather
than leaving a sleeping panel fetching 404s. `force` bypasses the whole thing,
and so does a lint error, which is checked first.

*Verified: [`tests/test_skip_unchanged.py`](../tests/test_skip_unchanged.py) —
`test_an_unchanged_frame_is_not_pushed_twice`,
`test_an_unchanged_frame_is_still_published_for_a_pull_transport` and
`test_a_pull_transport_skips_once_the_frame_is_servable`, one fake transport
with `pushes` toggled.*

## Full refresh and ghosting

E-ink ghosts. Partial refreshes are fast and quiet but leave the previous image
faintly behind; a flashing full refresh clears it and takes seconds.

`Engine._needs_full_refresh` decides per render: a panel whose profile has
`supports_partial = False` gets a full refresh every time, and otherwise the
cadence is `schedule.full_refresh_every`, falling back to the panel profile's
own recommendation when it is 0. `frames_since_full` resets on a full refresh
and increments on every other delivery — and only on *successful* delivery, so a
failed push does not consume the budget. `force` — `maverick render --force`,
the API's `?force=true`, or the MQTT full-refresh button — always takes it.

## Scheduling

Three ways a render starts, all landing in `Engine.render`
(`scheduling/scheduler.py`).

- **An interval.** `every: 5m`, parsed to seconds and handed to APScheduler as an
  interval trigger.
- **A cron expression.** Five fields, in the server's local time zone. This
  matters more than it looks for e-ink: a refresh is visible and slightly
  disruptive, so "on the hour during the day" is often the right rhythm.
  `every` and `cron` are mutually exclusive, and the config refuses both.
- **A state change.** `on_change: [sensor.x]` subscribes to Home Assistant's
  `state_changed` stream over the WebSocket API. Attribute-only updates are
  ignored — otherwise a panel re-renders every time an attribute timestamp ticks
  — and a burst is coalesced by `debounce` (default 10 s) into one render, since
  a thermostat reporting every few seconds would otherwise cost a panel refresh
  per reading.

**Quiet hours** (`23:00-06:30`, may wrap midnight) suppress scheduled and
state-triggered renders, because these panels end up in bedrooms and a full
refresh flashes the panel several times. Renders asked for by hand, by the API
or at startup ignore the window.

**`render_on_start`** renders once at startup rather than waiting out the first
interval — the difference between a panel that is right when you restart the
service and one that is blank for five minutes.

Two APScheduler settings are deliberate. `coalesce=True` means a backlog that
built up while the process was stopped collapses into one run: catching up on
nine missed renders would flash the panel nine times to arrive where one render
gets it. `misfire_grace_time=60` lets a job that was a little late still run,
and drops it beyond that, because a render from more than a minute ago is not
the frame anyone wanted. `max_instances=1` keeps a slow render from stacking on
itself.

## The Home Assistant surface today

Two surfaces, and only two. There is no custom integration and no `maverick.*`
action; [roadmap.md](roadmap.md) is where those live.

**MQTT discovery** (`ha/discovery.py`) is the primary one, and it is what makes
each display a real device rather than a URL to curl. Per display it publishes:

- `button.<name>_refresh` and `button.<name>_full_refresh` — press from an
  automation, a script, a dashboard or a voice assistant.
- `switch.<name>_scheduled_renders` — pause the timeline without editing YAML.
- `image.<name>` — what the panel is currently showing, visible in the HA UI and
  invaluable when the panel is in another room.
- `sensor.<name>_last_render`, `_status`, `_render_duration`, `_ink_coverage`,
  `_frames` and `binary_sensor.<name>_problem` — enough to alert on a panel that
  has silently stopped updating.

Commands travel back on `<base_topic>/display/+/command`, and `Application`
routes them into the engine. Discovery payloads are published retained so the
entities survive a Home Assistant restart, and a last will registered before the
client connects marks them unavailable if Maverick dies.

**The REST endpoint** is the escape hatch for anyone without a broker: `POST
/api/displays/{id}/render` from a `rest_command`, with `?force=true` for a full
redraw. It gives up the entities, the state sensors and the image, but it needs
no MQTT at all.

**Why MQTT is preferred over `set_state`.** `HomeAssistantClient.set_state` can
push a Maverick-owned entity straight into Home Assistant over the REST API with
no broker — and states created that way do not survive a Home Assistant restart.
An entity that vanishes on every HA restart is worse than no entity, because
automations referencing it break silently. MQTT discovery publishes retained, so
the broker replays the device on reconnect.

## What a prototype proved

A spike rendered real dashboards across five panel types before this code
existed. Four findings changed the design; each was a silent failure before it
was fixed, and each is now something the source enforces.

### Frontend auth

The Home Assistant frontend ignores an `Authorization` header; it reads a token
bundle from `localStorage.hassTokens`. A long-lived token works, but `hassUrl`
must match the origin **exactly**, scheme and port included. Any mismatch
silently redirects to the login page, which then screenshots as a blank frame.

*In the code: `build_auth_bundle` and the init script in
`render/dashboard.py` seed the bundle pre-navigation from
`home_assistant.render_url`; `_verify_authenticated` blocks delivery by raising
on the login page. No test reproduces this yet — it needs a browser.*

### Shadow DOM

The frontend is web components throughout, so a stylesheet on `document.head`
reaches almost nothing. Styles must be adopted into every shadow root, and
`attachShadow` patched to catch roots created by lazily-rendered cards.

*In the code: `_STYLE_SCRIPT` in `render/dashboard.py`. What the injected sheet
contains is covered by
[`tests/test_theme_palette_overrides.py`](../tests/test_theme_palette_overrides.py);
the adoption itself needs a browser and is not yet tested.*

### Physical type size

The one that surprised. Home Assistant hard-codes pixel font sizes, so a
`font-size` rule never reaches cards. Scaling via root `zoom` derived from panel
DPI does — and legibility on ink is governed by millimetres, not pixels.

*In the code: `build_css` in `eink/theme.py` emits one root `zoom` of
`round(3.2 mm in px) / 14`, so a card's own 13 px text lands at 2.97 mm @111 dpi,
3.04 mm @124 dpi and 2.99 mm @300 dpi. That the zoom is applied exactly once,
and composes with `render.zoom` rather than being replaced by it, is covered by
[`tests/test_render_zoom.py`](../tests/test_render_zoom.py).*

*Verified:
[`tests/test_physical_type_size.py`](../tests/test_physical_type_size.py) —
`test_card_text_lands_at_the_measured_physical_size` parses the `zoom` out of
the generated stylesheet and asserts each figure to ±0.05 mm.*

### Dithering

Naive error diffusion destroys dashboards. Text has more local contrast than
anything else on screen, so "dither where there's contrast" eats the glyphs.
Classifying by broad midtone fields (photographs, gradients) versus bimodal
regions (text) fixes it.

*In the code: `DitherMode.AUTO` and `_continuous_tone_mask` in `eink/dither.py`
diffuse only where the source is locally busy and snap flat regions to the
nearest ink; `dither_speckle` in `eink/lint.py` measures the result.*

*Verified:
[`tests/test_dither_preserves_text.py`](../tests/test_dither_preserves_text.py)
— against a synthetic dashboard,
`test_no_error_is_diffused_over_the_text` asserts the text quantises exactly as
plain nearest-ink does, `test_the_gradient_is_dithered_into_a_mixed_pattern`
that the gradient still gets both inks, and
`test_text_alone_quantises_without_speckle` that a page of text comes out below
a 0.001 speckle ratio — where
`test_diffusing_everywhere_speckles_the_same_text` shows plain Floyd–Steinberg
does not.*

The last two findings are reproducible from the project code alone, and the
tests named beside each one do that. The first two are not: both need a browser
to fail in, and nothing in the suite stands in for one.

## Known limitations

Stated plainly, because each one is the kind of thing a reader would otherwise
assume works.

- **Nothing has been tested on a physical panel.** Every panel-side claim in the
  catalogue — resolution, native rotation, refresh timing, measured ink values,
  ghosting cadence — comes from documentation, not a bench. No transport has
  been exercised end to end against real hardware.
- **There is no add-on and no custom integration.** No sidebar entry, no
  ingress, no config flow, no HACS listing, no `maverick.*` actions. Maverick is
  a standalone service that talks to Home Assistant over its APIs.
- **There is no Dockerfile.** Running it means a Python 3.11+ environment and
  systemd, as the README describes.
- **A display renders one dashboard.** There is no page list, no dwell time and
  no rotation, and nothing can change a display's dashboard at runtime.
- **Chromium is not bundled.** Playwright ships no aarch64 Linux build, so a
  Raspberry Pi needs the distro `chromium` package and `MAVERICK_CHROMIUM_PATH`.
- **The setup UI cannot change a display.** It shows what each panel rendered
  and what the linter found, offers refresh and full-refresh buttons, and links
  to the generated ESPHome config — but editing a display means editing the
  config file.
- **`maverick scan` needs a local Bluetooth adapter.** Tag discovery does not go
  through Home Assistant's Bluetooth proxies, even though delivery can.

## References

- [OpenDisplay Home Assistant integration](https://www.home-assistant.io/integrations/opendisplay/) — the `opendisplay.upload_image` action `transport.mode: ha` calls
- [py-opendisplay](https://github.com/OpenDisplay/py-opendisplay) — the library behind `transport.mode: ble`
- [ESPHome `online_image`](https://esphome.io/components/online_image.html) — what the generated ESPHome config uses to fetch a frame
- [TRMNL BYOS API](https://docs.usetrmnl.com/go/diy/byos) — the `/api/setup` and `/api/display` handshake the server implements
- [hass-lovelace-kindle-screensaver](https://github.com/sibbl/hass-lovelace-kindle-screensaver) — prior art for the Kindle path
- [tesserae](https://github.com/dmellok/tesserae) — prior art for e-ink dashboard layout
