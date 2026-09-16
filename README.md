# Maverick

[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

Maverick puts a live Home Assistant dashboard on an e-ink panel. It loads the
dashboard in headless Chromium, restyles it for ink — flattened cards, floored
font weights, type sized in millimetres rather than pixels — quantises the
result to the panel's measured ink colours with dithering that protects text,
refuses to ship a frame that is blank or illegible, and delivers it over BLE
(OpenDisplay), MQTT, HTTP pull (ESPHome, Kindle, TRMNL), a webhook, or a file.

On Home Assistant OS or Supervised it installs as a Home Assistant **app** —
what Home Assistant used to call an add-on — with Chromium built in; see
[Install as a Home Assistant app](#install-as-a-home-assistant-app). Anywhere
else it runs as a standalone service that talks to Home Assistant over its
APIs. What it is not yet: a custom integration, so displays become devices
through MQTT rather than a config flow.
[docs/architecture.md](docs/architecture.md) describes how the service works
today; [docs/roadmap.md](docs/roadmap.md) holds the plan for the rest.

## Status

Version 0.2.7.

The render service is implemented: the panel catalogue, the e-ink theme, the
image pipeline, the lint gate, five transports, the scheduler, the HTTP API and
the CLI all work.

A display can now be added, edited, previewed and removed from the setup UI
with no restart — the panel catalogue, the transport and every field of
`DisplayConfig` are all drawn from the running schema — and it keeps a bounded
render history and the pre-quantisation screenshot per display, both visible
on the card. See [Configuration](#configuration) and
[docs/architecture.md](docs/architecture.md) for how.

The Home Assistant app under [`app/`](app/DOCS.md) is built by CI on amd64,
which also launches Chromium inside the image and runs it behind ingress, so
**Open Web UI** embeds the setup UI in Home Assistant with no second login;
nobody has yet installed the app on a Home Assistant OS system. Treat it as a
first cut and report what you find.

**Nothing has been tested on a physical panel.** Every panel-side claim in the
catalogue — resolution, native rotation, refresh behaviour, measured ink values
— comes from documentation rather than a bench.

Transports exercised end to end against real hardware: **none — unverified.**
`http_pull` is covered by the test suite and by local runs against a stub, so
the server side of it works; whether a panel likes what it receives is untested.
The same caveat applies to `mqtt`, `opendisplay`, `file` and `webhook`.

## Install as a Home Assistant app

On Home Assistant OS or Supervised this is the short path. The button adds this
repository to the app store; the app itself lives in [`app/`](app/DOCS.md).

[![Open your Home Assistant instance and show the add app repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fambient-home-systems%2Fmaverick-eink-dashboard)

1. Click the button. The first time, my.home-assistant.io asks for your
   instance's address; then Home Assistant opens the app store with the
   repository dialog pre-filled, and you confirm **Add**. Or do it by hand:
   **Settings → Apps → App store**, the menu in the top right, **Repositories**,
   and add `https://github.com/ambient-home-systems/maverick-eink-dashboard`.
2. Open **Maverick** in the store and install it. The image is built on your
   machine, Chromium included, so the aarch64 note below does not apply; expect
   a few minutes.
3. On the **Configuration** tab, paste a long-lived access token (your profile
   → Security). Nothing else is required: the URL defaults to Home Assistant's
   internal address, and the Mosquitto broker app is picked up automatically
   when it is installed.
4. Start the app and open **Web UI**. The first start writes `maverick.yaml`,
   with one example display, into the app's configuration folder
   (`/addon_configs/…_maverick/`, reachable with the File editor, Studio Code
   Server or Samba apps), and copies its example display into
   `data/displays.yaml` beside it — the file that counts from then on, see
   [Configuration](#configuration).
5. Open the **Web UI** and add a display: **Add display**, give it a name,
   pick a panel from the catalogue and a dashboard from the picker, and save.
   The panel supplies the transport and everything else has a default, so
   there is nothing else to answer; *Advanced* is there when you want to
   disagree with one. No file to edit and no restart — the Web UI shows what
   each panel rendered and what the linter found, an **Edit** action on each
   card opens every field the display has, including a preview of a change
   before it is saved, and **Dashboard starter** hands you a Lovelace
   dashboard already sized for that panel.

[app/DOCS.md](app/DOCS.md) is the full page: every option, what the app maps
and exposes, and what to check when it does not start.

## Requirements

These are for a standalone install. The Home Assistant app bundles its own
Python and Chromium, so none of this applies there.

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

- **A Home Assistant credential that authenticates a frontend session.** A
  supervisor token will not do: it authenticates the REST API but not the
  frontend, and rendering a dashboard needs a frontend session.

  The easy way is to press **Link with Home Assistant** in Maverick's setup UI,
  which runs the same authorization flow the companion apps use and needs
  nothing copied by hand (`src/maverick/ha/auth.py`). It requires
  `server.base_url` to be set, because that is where Home Assistant redirects
  back to. Otherwise, create a long-lived access token under your profile →
  Security and set `home_assistant.token`.

  One rule matters more than any other here. `home_assistant.url` must be the
  exact origin your frontend is served from — scheme, host and port. The
  frontend reads its token out of `localStorage`, and it checks that the
  recorded origin matches the one it was loaded from. A trailing mismatch such
  as `http` against `https`, or a missing port, makes it redirect to the login
  screen, which then screenshots as a blank frame. Maverick detects that case
  and refuses to deliver, but it cannot fix the URL for you. See the module
  docstring at the top of `src/maverick/render/dashboard.py`.

## Install

On Home Assistant OS or Supervised, use
[the app](#install-as-a-home-assistant-app) instead. Everywhere else there is
no PyPI release yet, so install from git:

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

Installed [as the Home Assistant app](#install-as-a-home-assistant-app)? Open
its **Web UI** and use **Add display** — that is steps 1, 2 and 7 below done
for you, with a picker for the panel and the dashboard instead of YAML. The
rest of this section is the standalone path: a config file and the CLI loop
that goes with it, which is also how to iterate on a display's rendering
before touching a panel.

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

**4. Get a dashboard built for the panel.** Maverick renders a dashboard you
already have — but the one you already have was built for a phone, and a phone
dashboard on ink is the commonest reason a first render looks wrong. This
prints a Lovelace configuration sized for that panel's pixels and dpi, using
the cards that survive dithering and the entities you actually have:

```bash
maverick dashboard kitchen -o view.yaml
```

Paste it into Home Assistant under **Settings → Dashboards → Add dashboard**,
then **Edit → ⋮ → Raw configuration editor**, and point the display's
`dashboard` at the view path in it. Every choice it made is a comment in the
file; [docs/design-guide.md](docs/design-guide.md) is the long version, and
[the guide's short version](docs/guides/home-assistant.md#building-a-dashboard-for-e-ink)
is three rules.

**5. Render one frame, without sending it anywhere.**

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

**6. Look at the PNG.** `out/kitchen.png` is exactly what the panel will show,
at the panel's resolution and in its measured ink colours. This is the whole
point of the `--no-deliver` loop: iterate here, not by walking to the panel.
Run `maverick -v render kitchen --no-deliver -o out/` to see each lint
finding's hint, and see [docs/design-guide.md](docs/design-guide.md) for what
to change.

**7. Run the service.**

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

**The displays are the exception to "edit this file".** They are kept in a file
Maverick writes — `<data_dir>/displays.yaml`, or wherever `displays_file`
points — so that the setup UI can add and change one without a restart
(`src/maverick/store.py`). `load_config` resolves the two on every load, in the
one place every command goes through: if that store exists it is the source of
the displays and a `displays:` list here is ignored, with a warning naming both
files; if it does not, the list here is imported into it once and managed from
the UI afterwards. So write `displays:` to get started, then delete it — and
run `maverick check`, which prints which of the two files the displays it
validated came from.

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
  fails to load if it is unset. `${VAR:-default}` falls back instead, for a
  variable that is unset *or* empty, as `:-` does in a shell. Both work
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

[docs/reference/configuration.md](docs/reference/configuration.md) documents
every key, with its type, default and what the code does with it, plus the
options each transport takes. It is generated from the models, so it cannot
drift from the software.

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

**Device recipes.** [`docs/recipes/`](docs/recipes/README.md) has a page per
device path — [ESPHome and a Waveshare panel](docs/recipes/esphome-waveshare.md),
[OpenDisplay tags](docs/recipes/opendisplay-tags.md),
[Kindle and Kobo](docs/recipes/kindle-kobo.md), [TRMNL](docs/recipes/trmnl.md),
[a Pi driving an Inky over MQTT](docs/recipes/inky-mqtt.md), and
[webhook and file](docs/recipes/webhook-and-file.md) — each with the
configuration, a client where one is needed, and what to check when it does not
work. None of them has been run on hardware yet; each says what was verified.

## Home Assistant

**With MQTT.** Set `mqtt.enabled: true` and each display arrives in Home
Assistant as a device via MQTT discovery, with no YAML on the Home Assistant
side. Each device carries a **Refresh** button and a **Full refresh** button
(press either from an automation, a script, a dashboard or a voice assistant),
a **Scheduled renders** switch to pause the timeline without editing config, a
**Screen** image entity showing what the panel is currently displaying, a
**Page** select for a display with several
[pages](docs/architecture.md#pages), and diagnostic sensors for last render,
status, render duration, ink coverage, frames delivered and a problem flag. Entities are published retained so they
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

[docs/guides/home-assistant.md](docs/guides/home-assistant.md) is the full
guide to all of this: making the token, getting the URL rule right, the
automations worth writing, and rendering when your data changes rather than on
a timer.

Every topic, discovery payload, command word and state key is documented in
[docs/reference/mqtt.md](docs/reference/mqtt.md), which is what to read if you
are writing your own MQTT client rather than using Home Assistant.

## HTTP API

Interactive documentation is served at `/api/docs`, and the OpenAPI schema at
`/api/openapi.json`.

| Method | Route | Token? | What it does |
|---|---|---|---|
| GET | `/health` | no | Version, display count, Home Assistant and MQTT connection state |
| GET | `/api/panels` | no | The panel catalogue as JSON |
| GET | `/api/transports` | no | Registered transports |
| GET | `/api/schema/display` | yes | The display config JSON Schema, for a form to render |
| GET | `/api/ha/dashboards` | yes | Every Lovelace dashboard and its views, for the Dashboard field's picker; `503` when not connected to Home Assistant |
| GET | `/api/displays` | yes | Every display, with state, checksum and lint findings |
| GET | `/api/displays/{id}` | yes | One display |
| GET | `/api/displays/{id}/history` | yes | Past render outcomes for this display, newest first; `?limit=N`, default 20, maximum 50 |
| POST | `/api/displays` | yes | Create a display and start rendering it; `409` if the id exists |
| PUT | `/api/displays/{id}` | yes | Replace a display's configuration |
| DELETE | `/api/displays/{id}` | yes | Stop a display and delete its stored frames |
| POST | `/api/displays/{id}/schedule` | yes | Pause or resume a display's schedule at runtime |
| POST | `/api/displays/{id}/page` | yes | Put one of a display's pages on the panel: `{"index": n}`, `{"name": "..."}` or `{"step": 1}` |
| POST | `/api/displays/preview` | yes | Dry-run render of a candidate config; saves nothing |
| POST | `/api/displays/{id}/render` | yes | Render one display now; `?force=true` ignores the unchanged and lint gates; `?wait=false` returns `202` and renders in the background |
| POST | `/api/render` | yes | Render every enabled display |
| GET | `/api/displays/{id}/frame` | yes | The current frame, in the panel's wire format |
| GET | `/api/displays/{id}/preview.png` | yes | The frame as a viewable PNG |
| GET | `/api/displays/{id}/screenshot.png` | yes | The pre-quantisation capture, downscaled to panel resolution |
| GET | `/api/displays/{id}/esphome.yaml` | yes | A ready-to-flash ESPHome config for this display |
| GET | `/api/auth/status` | no | What Home Assistant credential Maverick currently holds, and whether linking is possible |
| GET | `/api/auth/start` | yes | The URL to send the browser to, to link a Home Assistant account |
| GET | `/api/auth/callback` | no* | Where Home Assistant sends the browser back; authenticated by its own single-use nonce instead of the token |
| GET | `/api/setup` | no | TRMNL bring-your-own-server handshake |
| GET | `/api/display` | no | TRMNL frame pointer |
| GET | `/` | yes | The setup UI |
| GET | `/static/{file}` | no | The setup UI's stylesheet and script |
| GET | `/api/docs` | no | Swagger UI over the OpenAPI schema |

The token column applies only when `server.api_token` is set; leave it empty
and nothing is gated. Clients may present it as `Authorization: Bearer`, as an
`Access-Token` header, or as a `?token=` query parameter. The UI asks for the
token when it needs one and keeps it for the tab, so `/?token=...` is no longer
the only way in. `/api/auth/callback` (marked `no*`) can never sit behind the
token — Home Assistant redirects a browser to it and knows nothing of
`server.api_token` — so it is authenticated instead by a single-use nonce
minted by `/api/auth/start`, which is behind the token.

Requests arriving through the Home Assistant app's ingress are exempt, since
Home Assistant authenticates them before the app sees them; they are recognised
by the peer address of the Supervisor's ingress proxy, and only while Maverick
is running as an app (`src/maverick/ha/supervisor.py`).

Two details on `/api/displays/{id}/frame` matter to battery devices. The
response carries a strong `ETag`, and a device that sends it back as
`If-None-Match` gets `304 Not Modified` with no body — which saves the download
and, far more importantly, saves the e-ink refresh, since a refresh costs
orders of magnitude more energy than the fetch. The response also carries
`X-Maverick-Next-Refresh`, the number of seconds the device may sleep before
asking again, taken from that display's schedule interval and defaulting to 900
for cron schedules.

[docs/reference/http-api.md](docs/reference/http-api.md) documents every route
in full — request parameters, response keys, status codes, and the complete
pull protocol a battery panel needs.

## Running as a service

On Home Assistant OS or Supervised, [the app](#install-as-a-home-assistant-app)
is the packaged way to run it. Everywhere else, run it as a Docker container or
under systemd.

### Docker

The root [`Dockerfile`](Dockerfile) builds a standalone image — distinct from
[`app/Dockerfile`](app/Dockerfile), which the Supervisor builds and which
expects the Home Assistant app's own options file. This one takes a config file
by volume instead:

```bash
docker build -t maverick .
docker run -d \
  --name maverick \
  -p 5000:5000 \
  -v /path/to/maverick.yaml:/config/maverick.yaml \
  -v /path/to/media:/media \
  -v /path/to/share:/share \
  maverick
```

`/config/maverick.yaml` is the config file (`config.example.yaml` is a starting
point); `/media` and `/share` are volumes a display's `dashboard` might
reference through Home Assistant's own media or local file paths. The image
installs Debian's `chromium` package and sets `MAVERICK_CHROMIUM_PATH` itself,
the same way [`app/Dockerfile`](app/Dockerfile) does, so no separate Chromium
install is needed inside the container.

To develop against a real Home Assistant instead of production, see "Running
against a real Home Assistant" in
[CONTRIBUTING.md](CONTRIBUTING.md#running-against-a-real-home-assistant), which
stands up Home Assistant, Mosquitto and this image together with
`docker-compose.dev.yml`.

### systemd

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
ExecStart=/opt/maverick/.venv/bin/maverick -c /opt/maverick/config.yaml serve
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
token, will not work. If you linked an account rather than pasting a token, the
message names that case instead and the fix is to link again — refresh tokens
appear in the same profile page and can be deleted there.

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
python scripts/gen_docs.py --check   # the reference pages match the code
python scripts/check_links.py        # every relative Markdown link resolves
```

Those four are what [CI](.github/workflows/ci.yml) runs, on Python 3.11 and
3.12. None of them needs Chromium: the suite stubs the renderer and the
transports, so it runs in a bare checkout.

`docs/reference/` is generated. Change a field in `src/maverick/config.py` — or
the hand-written prose in `docs/reference/_configuration.intro.md` — then run
`python scripts/gen_docs.py` and commit what it writes. Editing a generated page
by hand is undone by the next run, and `--check` fails the build meanwhile.

`MAVERICK_DEBUG=1` makes the CLI raise instead of printing a one-line error, so
you get a traceback. [CONTRIBUTING.md](CONTRIBUTING.md) is the full contributor
guide, [CLAUDE.md](CLAUDE.md) the short version for AI-assisted changes, and
[docs/README.md](docs/README.md) indexes every page in the documentation set.

## Roadmap

From [docs/roadmap.md](docs/roadmap.md):

- **An integration**: one device per panel and actions that need no MQTT
  broker, distributed via HACS with a config flow instead of a pasted token.
  UI setup itself is done — see [Status](#status) — without it; MQTT
  discovery stays the way a display becomes a Home Assistant device either
  way.
- **A control surface for Home Assistant**: a tile feature, a card with a live
  thumbnail and a fleet view. Pages themselves are built — an ordered list of
  dashboards per display with dwell times and rotation, a Page select on each
  device and `POST /api/displays/{id}/page` — and the card would be a client
  over them.
- **A dashboard strategy and live preview**, so a correct e-ink dashboard is
  generated for you and you can see the real quantised output while editing a
  Home Assistant dashboard itself, rather than only in the setup UI's own
  preview of a display's configuration.

## Licence

MIT. See [LICENSE](LICENSE).
