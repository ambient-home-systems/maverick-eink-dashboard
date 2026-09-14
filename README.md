# Maverick

[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

Maverick puts a live Home Assistant dashboard on an e-ink panel. It loads the
dashboard in headless Chromium, restyles it for ink — flattened cards, floored
font weights, type sized in millimetres rather than pixels — quantises the
result to the panel's measured ink colours with dithering that protects text,
refuses to ship a frame that is blank or illegible, and delivers it over BLE
(OpenDisplay), MQTT, HTTP pull (ESPHome, Kindle, TRMNL), a webhook, or a file.

What it is not yet: there is no Home Assistant add-on and no custom
integration. Maverick runs as a standalone service and talks to Home Assistant
over its APIs. [docs/architecture.md](docs/architecture.md) holds the roadmap
for both.

## Status

Version 0.1.0.

The render service is implemented: the panel catalogue, the e-ink theme, the
image pipeline, the lint gate, five transports, the scheduler, the HTTP API and
the CLI all work.

**Nothing has been tested on a physical panel.** Every panel-side claim in the
catalogue — resolution, native rotation, refresh behaviour, measured ink values
— comes from documentation rather than a bench.

Transports exercised end to end against real hardware: **none — unverified.**
`http_pull` is covered by the test suite and by local runs against a stub, so
the server side of it works; whether a panel likes what it receives is untested.
The same caveat applies to `mqtt`, `opendisplay`, `file` and `webhook`.

## Requirements

- **Python 3.11 or newer.**
- **Chromium**, installed through Playwright:

  ```bash
  playwright install chromium
  ```

  On **aarch64** — a Raspberry Pi, or Home Assistant OS on ARM — Playwright
  ships no Linux ARM build and this command gives you nothing usable. Install
  your distribution's Chromium instead and point Maverick at it:

  ```bash
  sudo apt install chromium
  export MAVERICK_CHROMIUM_PATH=/usr/bin/chromium
  ```

  Maverick reads `MAVERICK_CHROMIUM_PATH` when it launches the browser
  (`src/maverick/render/browser.py:51-56`).

- **A Home Assistant long-lived access token**, created under your profile →
  Security. A supervisor token will not do: it authenticates the REST API but
  not the frontend, and rendering a dashboard needs a frontend session.

  One rule matters more than any other here. `home_assistant.url` must be the
  exact origin your frontend is served from — scheme, host and port. The
  frontend reads its token out of `localStorage`, and it checks that the
  recorded origin matches the one it was loaded from. A trailing mismatch such
  as `http` against `https`, or a missing port, makes it redirect to the login
  screen, which then screenshots as a blank frame. Maverick detects that case
  and refuses to deliver, but it cannot fix the URL for you. See the module
  docstring at the top of `src/maverick/render/dashboard.py`.

## Install

There is no PyPI release yet, so install from git:

```bash
pip install "maverick-eink-dashboard @ git+https://github.com/ambient-home-systems/maverick-eink-dashboard"
```

For OpenDisplay BLE tags talked to directly from this machine, add the
`opendisplay` extra. You do not need it if you deliver through Home Assistant's
Bluetooth instead:

```bash
pip install "maverick-eink-dashboard[opendisplay] @ git+https://github.com/ambient-home-systems/maverick-eink-dashboard"
```

To work on Maverick itself, clone it and install the `dev` extra:

```bash
git clone https://github.com/ambient-home-systems/maverick-eink-dashboard
cd maverick-eink-dashboard
pip install -e ".[dev]"
```

Then install Chromium once, as above.

## Quick start

**1. Write a config.**

```bash
maverick init > config.yaml
```

That prints [config.example.yaml](config.example.yaml), commented, with two
displays in it.

**2. Edit three keys.** They are marked `CHANGE ME` in the file:

- `home_assistant.url` — the exact origin your frontend is served from.
- `home_assistant.token` — read from `${HA_TOKEN}` by default, so
  `export HA_TOKEN=...` is enough.
- `server.base_url` — where panels that pull frames should fetch from. It has
  to be reachable *from the panel*, not just from your laptop.

**3. Check it.** This validates the config and the connection to Home
Assistant, and renders nothing:

```bash
maverick check
```

```text
config: 2 display(s)
    kitchen          Waveshare 7.5" monochrome (V2)
      800x480 mono 124dpi rot0 -> http_pull
      dashboard: /lovelace-eink/kitchen
    hallway-tag      Solum 2.9" BWR (OpenDisplay)
      296x128 bwr 111dpi rot0 -> opendisplay
      dashboard: /lovelace-eink/tag
home assistant: ok (http://homeassistant.local:8123, 2026.9.1)
mqtt: disabled (displays will not appear as Home Assistant entities)
OK
```

It exits non-zero and prints `N problem(s) found` if anything is wrong.

**4. Render one frame, without sending it anywhere.**

```bash
maverick render kitchen --no-deliver -o out/
```

```text
[kitchen] ok in 5.24s (render 4.91s) lint=1 warning — 
    warning  hairlines: 89% of inked pixels are one pixel wide.
    wrote out/kitchen.png
```

*(Both transcripts above are illustrative: they are the real output format, but
they were produced against a stand-in for Home Assistant rather than a live
instance. Your timings, version and lint findings will differ.)*

**5. Look at the PNG.** `out/kitchen.png` is exactly what the panel will show,
at the panel's resolution and in its measured ink colours. This is the whole
point of the `--no-deliver` loop: iterate here, not by walking to the panel.
Run `maverick -v render kitchen --no-deliver -o out/` to see each lint
finding's hint, and see [docs/design-guide.md](docs/design-guide.md) for what
to change.

**6. Run the service.**

```bash
maverick serve
```

That starts the HTTP server, the scheduler and the Home Assistant state
listeners together. Open `http://localhost:5000/` for the setup UI.

## Configuration

Maverick reads `config.yaml` from the working directory, or
`/config/maverick.yaml`, or `~/.config/maverick/config.yaml` — in that order.
`-c path/to/file.yaml` overrides all of them.

The file has four sections:

```yaml
home_assistant:   # url, token, verify_ssl, frontend_url
mqtt:             # broker connection; off by default
server:           # host, port, base_url, api_token, enable_ui
displays:         # a list, one entry per physical panel
  - id: kitchen
    panel: waveshare-7in5-mono
    dashboard: /lovelace-eink/kitchen
```

Those three keys are all a display needs. `id` is the only one with no default
at all; `panel` and `dashboard` have defaults (`generic-mono` and
`/lovelace/0`) that are unlikely to be what you want, so write them. Everything
else — resolution, colour scheme, DPI, native rotation, wire format, how often
to do a flashing full refresh — is derived from the panel profile in the
catalogue. Every derived value stays
overridable, per display, for the panel mounted sideways behind glass. The
optional blocks are `theme`, `image`, `render`, `schedule`, `transport`, `pack`
and `esphome`.

Three things to know about the format:

- **Environment substitution.** `${VAR}` inserts an environment variable and
  fails to load if it is unset. `${VAR:-default}` falls back instead. Both work
  anywhere in the file, so no secret has to live in it.
- **Durations are strings.** `30s`, `5m`, `1h`, `2d`, or a bare number of
  seconds. They appear in `schedule.every`, `schedule.debounce`,
  `render.settle` and `render.timeout`.
- **Unknown keys are errors.** The schema is strict (`extra="forbid"`,
  `src/maverick/config.py:76`), so a misspelt key fails the load and names
  itself rather than being silently ignored:

  ```text
  error: ValidationError: 1 validation error for Config
  home_assistant.urll
    Extra inputs are not permitted [type=extra_forbidden, input_value='http://x', input_type=str]
  ```

  The exception is the `transport` block, which accepts whatever options its
  transport defines.

[config.example.yaml](config.example.yaml) is the commented reference: two
displays, one pulling over HTTP on a timer with quiet hours, one OpenDisplay
tag re-rendering when an entity changes.

## Panels

List everything in the catalogue:

```bash
maverick panels
```

```text
waveshare
  waveshare-10in3-gray16            1872x1404  gray16     227dpi  partial
  waveshare-13in3-gray16            1600x1200  gray16     150dpi  partial
  waveshare-2in13-mono               250x122   mono       131dpi  partial
  waveshare-2in9-mono                296x128   mono       111dpi  partial
  waveshare-4in2-bwr                 400x300   bwr        119dpi  full-only
  waveshare-4in2-mono                400x300   mono       119dpi  partial
  waveshare-5in65-acep               600x448   acep7      132dpi  full-only
  waveshare-7in3-spectra             800x480   spectra6   128dpi  full-only
  waveshare-7in5-bwr                 800x480   bwr        124dpi  full-only
  waveshare-7in5-mono                800x480   mono       124dpi  full-only
```

That is one vendor of ten. Add `maverick -v panels` for each profile's notes,
or `maverick panels --json` for the machine-readable form.

There are **28 profiles**, covering Waveshare, Solum and other OpenDisplay
shelf-label tags, Pimoroni Inky, Seeed, LilyGO, jailbroken Kindles, Kobo and
TRMNL.

If your panel is not listed, use the `generic-mono` fallback and override its
geometry:

```yaml
displays:
  - id: spare
    panel: generic-mono
    width: 640
    height: 384
    dashboard: /lovelace-eink/spare
```

`color_scheme`, `dpi`, `rotation` and `frame_format` override the same way.
`dpi` is worth getting roughly right: it is what converts pixel sizes into
millimetres, and millimetres are what govern legibility on ink.

## Transports

```bash
maverick transports
```

| Transport | Direction | Suits | Required options |
|---|---|---|---|
| `mqtt` | push | Always-on clients holding a subscription: a Pi driving an Inky, an ESPHome node, a custom client | none; needs `mqtt.enabled: true` globally. Optional `topic` (defaults to `<base_topic>/display/<id>`) |
| `opendisplay` | push | OpenDisplay BLE e-paper tags, including reflashed Solum shelf labels | `mode: ha` needs `device_id`. `mode: ble` needs `mac` or `device_name` |
| `http_pull` | pull | Battery devices that wake, fetch and sleep: ESP32, ESPHome, Kindle, TRMNL | none; needs `server.base_url` set to something the panel can reach |
| `file` | push | Anything that reads a file: a Kindle screensaver over rsync, a Samba share, a separate web server, or eyeballing output | none. Optional `path` (defaults to `./out`), `filename`, `write_preview` |
| `webhook` | push | The escape hatch: any device or service with an HTTP endpoint | `url`. Optional `method`, `headers`, `timeout` |

The push/pull split is real, not cosmetic. A push transport succeeds or fails
now and the scheduler learns which. A pull transport cannot deliver anything —
it publishes the frame and reports `awaiting_pull`, and the real confirmation
arrives later when the device fetches.

For `opendisplay` in `mode: ble`, run `maverick scan` to find tags in range.
It needs a Bluetooth adapter the process can see, and the `opendisplay` extra.

## Home Assistant

**With MQTT.** Set `mqtt.enabled: true` and each display arrives in Home
Assistant as a device via MQTT discovery, with no YAML on the Home Assistant
side. Each device carries a **Refresh** button and a **Full refresh** button
(press either from an automation, a script, a dashboard or a voice assistant),
a **Scheduled renders** switch to pause the timeline without editing config, a
**Screen** image entity showing what the panel is currently displaying, and
diagnostic sensors for last render, status, render duration, ink coverage,
frames delivered and a problem flag. Entities are published retained so they
survive a Home Assistant restart, and a last will marks them unavailable if
Maverick dies. This is `src/maverick/ha/discovery.py`.

**Without MQTT.** Trigger renders with a `rest_command` against
`POST /api/displays/{id}/render`:

```yaml
# configuration.yaml
rest_command:
  maverick_render:
    url: "http://maverick.local:5000/api/displays/{{ display }}/render"
    method: post
    # Only needed if server.api_token is set.
    headers:
      authorization: !secret maverick_authorization
```

```yaml
# secrets.yaml
maverick_authorization: "Bearer your-api-token"
```

Then call it from an automation:

```yaml
action: rest_command.maverick_render
data:
  display: kitchen
```

**OpenDisplay through Home Assistant's Bluetooth.** With `mode: ha` — the
default — Maverick hands the frame to Home Assistant's own
`opendisplay.upload_image` action, and Home Assistant delivers it over whatever
Bluetooth it has, **including ESPHome Bluetooth proxies**. A proxy in the room
with the tag beats a server in a cupboard, and Maverick needs no Bluetooth
hardware at all. Two things are required: `device_id`, which is the device
registry id from the OpenDisplay integration rather than an entity id or a MAC,
and a media path both processes can see — Maverick writes the PNG to
`media_dir` (default `/media/maverick`) and Home Assistant reads it back from
its media folder. See `src/maverick/transports/opendisplay.py:9-27`.

## HTTP API

Interactive documentation is served at `/api/docs`, and the OpenAPI schema at
`/api/openapi.json`.

| Method | Route | Token? | What it does |
|---|---|---|---|
| GET | `/health` | no | Version, display count, Home Assistant and MQTT connection state |
| GET | `/api/panels` | no | The panel catalogue as JSON |
| GET | `/api/transports` | no | Registered transports |
| GET | `/api/displays` | yes | Every display, with state, checksum and lint findings |
| GET | `/api/displays/{id}` | yes | One display |
| POST | `/api/displays/{id}/render` | yes | Render one display now; `?force=true` ignores the unchanged and lint gates |
| POST | `/api/render` | yes | Render every enabled display |
| GET | `/api/displays/{id}/frame` | yes | The current frame, in the panel's wire format |
| GET | `/api/displays/{id}/preview.png` | no | The frame as a viewable PNG |
| GET | `/api/displays/{id}/esphome.yaml` | no | A ready-to-flash ESPHome config for this display |
| GET | `/api/setup` | no | TRMNL bring-your-own-server handshake |
| GET | `/api/display` | no | TRMNL frame pointer |
| GET | `/` | no | The setup UI |

The token column applies only when `server.api_token` is set; leave it empty
and nothing is gated. Clients may present it as `Authorization: Bearer`, as an
`Access-Token` header, or as a `?token=` query parameter.

Two details on `/api/displays/{id}/frame` matter to battery devices. The
response carries a strong `ETag`, and a device that sends it back as
`If-None-Match` gets `304 Not Modified` with no body — which saves the download
and, far more importantly, saves the e-ink refresh, since a refresh costs
orders of magnitude more energy than the fetch. The response also carries
`X-Maverick-Next-Refresh`, the number of seconds the device may sleep before
asking again, taken from that display's schedule interval and defaulting to 900
for cron schedules.

## Running as a service

There is **no Dockerfile and no Home Assistant add-on yet**; both are on the
roadmap in [docs/architecture.md](docs/architecture.md). For now, run it under
systemd.

```ini
# /etc/systemd/system/maverick.service
[Unit]
Description=Maverick e-ink dashboard renderer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=maverick
WorkingDirectory=/opt/maverick
Environment=HA_TOKEN=your-long-lived-access-token
# On aarch64, where Playwright ships no Chromium build:
# Environment=MAVERICK_CHROMIUM_PATH=/usr/bin/chromium
ExecStart=/opt/maverick/.venv/bin/maverick serve -c /opt/maverick/config.yaml
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now maverick
sudo journalctl -u maverick -f
```

Chromium wants more memory than the service itself does. Rendering two
1872×1404 dashboards at `supersample: 2` at the same time is roughly a 250 MB
spike, so give a small board some headroom or lower `supersample`.

## Troubleshooting

The five failures people hit first. [docs/troubleshooting.md](docs/troubleshooting.md)
covers the rest.

**1. A blank frame, or the login page.** The render is blocked rather than
delivered, and you see one of:

```text
[kitchen] Home Assistant redirected to the login page. The access token is missing, expired or was issued for a different URL — home_assistant.url must match the origin the token was created on, scheme and port included.
```

```text
[kitchen] skipped: blocked by lint: Frame is 99.63% 'white' — the dashboard almost certainly did not render.
```

Fix `home_assistant.url` so it is byte-for-byte the origin your frontend is
served from, port included. If the frontend lives somewhere other than the API
— behind a reverse proxy — set `frontend_url` to the frontend's origin and
leave `url` pointing at the API. To see what Chromium actually saw, set
`render.debug_artifacts: true` on the display and re-run; the raw screenshot is
written to `<data_dir>/debug/<id>/screenshot.png`.

**2. The token is rejected.**

```text
home assistant: FAILED — Home Assistant rejected the token (401). Long-lived access tokens are bound to the instance that issued them.
```

Create a new long-lived access token under your profile → Security, on the
instance you are pointing at. A token from another instance, or a supervisor
token, will not work.

**3. Chromium will not start.**

```text
RuntimeError: Could not start Chromium: ...
Run `playwright install chromium`, or point MAVERICK_CHROMIUM_PATH at an existing Chromium binary.
```

Run `playwright install chromium`. On aarch64 that will not help — Playwright
ships no Linux ARM build — so install the distribution package and set
`MAVERICK_CHROMIUM_PATH=/usr/bin/chromium`.

**4. The MQTT broker never connects.**

```text
Timed out connecting to the MQTT broker at core-mosquitto:1883. Check mqtt.host, credentials, and that the broker is running.
```

`core-mosquitto` is the default and only resolves inside Home Assistant's own
network. Running standalone, set `mqtt.host` to the broker's address on your
network.

**5. An unknown panel.**

```text
error: KeyError: "Unknown panel 'waveshare-7in5'. Run `maverick panels` to list all 28 supported panels. Did you mean: opendisplay-solum-7in5-bwr, opendisplay-xiao-7in5, trmnl-7in5, waveshare-10in3-gray16, waveshare-13in3-gray16?"
```

Run `maverick panels` and copy the id exactly. If your panel genuinely is not
listed, use `generic-mono` with `width` and `height` overrides.

## Development

```bash
pip install -e ".[dev]"
ruff check
pytest
```

`MAVERICK_DEBUG=1` makes the CLI raise instead of printing a one-line error, so
you get a traceback. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Roadmap

From [docs/architecture.md](docs/architecture.md):

- **An add-on and an integration**, so Maverick installs as an app: a sidebar
  entry with ingress, UI setup instead of YAML, one device per panel, and
  actions that need no MQTT broker.
- **Pages and a control surface**: an ordered list of dashboards per display
  with rotation and dwell times, plus a tile feature and a card to drive it.
- **A dashboard strategy and live preview**, so a correct e-ink dashboard is
  generated for you and you can see the real quantised output while editing.

## Licence

MIT. See [LICENSE](LICENSE).
