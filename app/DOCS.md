# Maverick

Maverick puts a live Home Assistant dashboard on an e-ink panel. This app
packages the service for Home Assistant OS and Supervised installations, with
Chromium built in, so the aarch64 caveat in the project README does not apply
here: the image installs Debian's own `chromium` package.

> **Not yet run on a Home Assistant installation by the project.** CI builds
> this image on amd64 and launches Chromium inside it; what happens on a real
> Home Assistant OS system, a Raspberry Pi in particular, is unreported. Please
> open an issue with what you saw:
> https://github.com/ambient-home-systems/maverick-eink-dashboard/issues.
> [What was verified](#what-was-verified) says exactly how far this page was
> checked.

Home Assistant now calls what used to be add-ons *apps*. Older pages in the
project still say add-on; the two words mean the same thing.

## Install

1. Add this repository to the app store:

   [![Open your Home Assistant instance and show the add app repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fambient-home-systems%2Fmaverick-eink-dashboard)

   The first time, my.home-assistant.io asks for your instance's address; then
   Home Assistant opens the app store with the repository dialog pre-filled,
   and you confirm **Add**. Or by hand: **Settings → Apps → App store**, the
   menu in the top right, **Repositories**, and paste
   `https://github.com/ambient-home-systems/maverick-eink-dashboard`.
2. Open **Maverick** in the store and install it. There is no pre-built image
   yet, so the Supervisor builds one on your machine. Expect a few minutes and
   a few hundred megabytes: Debian's Chromium is most of it.
3. **Start** the app and open its **Web UI**.
4. Press **Link with Home Assistant**. You will be asked to log in once, and
   sent straight back. That is the whole setup.

   Rendering needs a *frontend* session, which the Supervisor's own token
   cannot open, so the app has to hold a credential of its own. Linking obtains
   one through the same authorization flow the companion apps use, and saves it
   to this app's options for you. If you would rather do it by hand, paste a
   long-lived access token (your user profile, **Security** tab) into
   `home_assistant_token` on the **Configuration** tab instead.

The first start writes `maverick.yaml`, with one example display, into the
app's configuration folder, and copies that example display into
`data/displays.yaml` beside it. Open the **Web UI** and use **Add display** to
describe your own panels — no file to edit and no restart; see
[The configuration file](#the-configuration-file) for where displays actually
live and [docs/architecture.md](https://github.com/ambient-home-systems/maverick-eink-dashboard/blob/main/docs/architecture.md#changing-a-display-while-the-service-runs)
for how a change reaches the running service.

## Options

| Option | Required | Default | What it does |
| --- | --- | --- | --- |
| `home_assistant_url` | yes | `http://homeassistant:8123` | The origin the app loads dashboards from. Inside the app network, `homeassistant` is Home Assistant Core. Change it only if Home Assistant is served over HTTPS or on another port; it must match the origin the frontend is loaded from, scheme and port included, or every render becomes a login page and is refused. |
| `home_assistant_token` | no | | A long-lived access token from your profile's Security tab. Rendering needs a frontend session, and the Supervisor's own token cannot open one. Leave it empty and use **Link with Home Assistant** in the Web UI instead. |
| `home_assistant_refresh_token` | no | | Written for you when you link an account. No need to touch it; clear it to unlink. |
| `home_assistant_client_id` | no | | Written alongside the refresh token. Home Assistant needs it to renew the session, so the two only work as a pair. |
| `log_level` | yes | `info` | `debug`, `info`, `warning` or `error`. |
| `base_url` | no | derived | Where panels that pull frames (`http_pull`) should fetch from, reachable *from the panel*, for example `http://192.168.1.10:5000`. Left empty, the app uses the host's first IPv4 address on port 5000 and says so in the log. |
| `api_token` | no | | Gates the HTTP API, the preview images and this app's own web UI on the published port. Panels and `rest_command`s must then send it as `Authorization: Bearer`, `Access-Token` or `?token=`; the web UI asks for it and remembers it for the tab. Opening the UI from inside Home Assistant is unaffected — those requests come through the app's ingress, which Home Assistant has already put a login in front of. Setting it also makes each panel's `image` entity receive its frame over MQTT rather than as a URL, because Home Assistant fetches an image URL without credentials. |
| `mqtt_host` | no | | A broker to use instead of the Mosquitto broker app. Leave it empty and the Mosquitto app, when installed, is used automatically with the credentials it hands the Supervisor. |
| `mqtt_port` | no | `1883` | Only read when `mqtt_host` is set. |
| `mqtt_username` | no | | Only read when `mqtt_host` is set. |
| `mqtt_password` | no | | Only read when `mqtt_host` is set. |

Maverick reads these options itself, from the file the Supervisor writes them
to, and turns them into the environment variables `maverick.yaml` substitutes
through `${VAR}` — so the connection details come from this tab and everything
else from the file
([`src/maverick/ha/options.py`](https://github.com/ambient-home-systems/maverick-eink-dashboard/blob/main/src/maverick/ha/options.py)).
The app log says what it made of them on every start: which broker it found,
which address panels will be told to fetch from, and whether a credential is
missing.

## The configuration file

The app writes `maverick.yaml` on its first start into its own configuration
folder, which Home Assistant exposes as `/addon_configs/<something>_maverick/`
to the **File editor**, **Studio Code Server** and **Samba share** apps — as it
does `data/`, where the frames, the state and the displays live. It looks like
this:

```yaml
home_assistant:
  url: ${HA_URL}            # from the Configuration tab; leave as is
  token: ${HA_TOKEN}
mqtt:
  enabled: ${MQTT_ENABLED:-false}
  # ...
server:
  base_url: ${MAVERICK_BASE_URL:-}
  # ...
data_dir: /config/data
displays:
  - id: kitchen
    panel: waveshare-7in5-mono
    dashboard: /lovelace/0
    schedule:
      every: 5m
      quiet_hours: "23:00-06:30"
    transport:
      type: http_pull
```

Leave the substituted blocks (`home_assistant:`, `mqtt:`, `server:`) alone —
they come from the Configuration tab. The `displays:` list shown above is only
the example the app wrote on its first start; add or change a display from the
**Web UI**'s **Add display** dialog and per-card **Edit** drawer instead of
editing it, since that is what actually reaches the running service.

The displays are the one part of this file that moves. The first start copies
the `displays:` list into `data/displays.yaml` — in the same folder, beside the
frames and the state — and that file is the source of the displays from then
on, because it is one Maverick itself can write
(`src/maverick/store.py`). The `displays:` list here is ignored once it exists,
with a line in the log naming both files. A display added, edited or removed
in the Web UI is written straight to `data/displays.yaml` and applied
immediately, with no restart; hand-editing that file works too, but needs a
restart to be picked up, same as editing `displays:` here would have. Deleting
`data/displays.yaml` brings the list in this file back at the next start.

The full key reference is
[docs/reference/configuration.md](https://github.com/ambient-home-systems/maverick-eink-dashboard/blob/main/docs/reference/configuration.md),
the panel ids are in
[docs/reference/panels.md](https://github.com/ambient-home-systems/maverick-eink-dashboard/blob/main/docs/reference/panels.md),
and there is a recipe per device path under
[docs/recipes/](https://github.com/ambient-home-systems/maverick-eink-dashboard/blob/main/docs/recipes/README.md).

Restart the app after hand-editing `maverick.yaml` or `data/displays.yaml`;
changes made from the Web UI apply immediately, with no restart. Unknown keys
are errors either way, and the log names the key.

## Panels that pull frames

An ESPHome board, a Kindle or a TRMNL fetches its frame from
`http://<home-assistant-host>:5000/api/displays/<id>/frame`. Port 5000 is
exposed on the host for exactly this. `base_url` is what the app tells such
devices (it appears in the generated ESPHome configuration and the TRMNL
handshake), so it has to be an address the panel can reach: set it if the
derived one is wrong, for instance when Home Assistant has more than one
network.

For an ESP32 board, each display's card in the Web UI has an **Install on
device** step: the secrets the generated ESPHome configuration expects, the
configuration itself, and **Send to ESPHome**, which writes it into the
ESPHome Device Builder add-on's own folder so the device appears there ready
to install. That needs the ESPHome add-on installed, and is why this app maps
`/addon_configs` read-write. The Device Builder does the compiling and the
flashing; this app never does.

## MQTT and devices

With a broker, every display becomes a Home Assistant device through MQTT
discovery: a Refresh and a Full refresh button, a Scheduled renders switch, a
Screen image entity, and diagnostic sensors. Install the **Mosquitto broker**
app and nothing more is needed; the app picks up its host and credentials. To
use another broker, set `mqtt_host`.

## OpenDisplay tags

The app has no Bluetooth of its own. OpenDisplay tags are delivered through
Home Assistant's Bluetooth instead (`transport: {type: opendisplay, mode: ha,
device_id: ...}`), including ESPHome Bluetooth proxies. That path writes the
frame to `/media/maverick/<id>.png` and calls the `opendisplay.upload_image`
action with it, which is why the app maps `/media` read-write. `mode: ble`
needs an adapter the process can see; inside the app it is refused with a
message saying so, and the Web UI does not offer it.

Set up the OpenDisplay integration first and the Web UI does the rest: every
tag it has found is listed above the display cards with a guessed panel, and
**Add as display** fills in the panel, the transport and the device id. The
Add and Edit dialogs pick the tag from a list rather than asking for its
registry id, and **Test delivery** checks the id against Home Assistant
before anything is saved.

## Files and folders

| Inside the app | Where you see it | What is there |
| --- | --- | --- |
| `/config/maverick.yaml` | `/addon_configs/<something>_maverick/maverick.yaml` | The configuration. Survives updates and reinstalls. |
| `/config/data/displays.yaml` | `data/displays.yaml` | The displays themselves, written by the Web UI (or by hand) — the source of truth once it exists; see [The configuration file](#the-configuration-file). |
| `/config/data/` | the same folder, `data/` | The frame store — the current frame, its preview PNG and the pre-quantisation screenshot per display — plus `state.json` and `debug/<id>/` when a display sets `render.debug_artifacts: true`. |
| `/config/data/history/` | `data/history/` | Up to the last 50 render outcomes per display, `<id>.json`, behind each card's History disclosure and `GET /api/displays/{id}/history`. |
| `/media/maverick/` | Home Assistant's media folder | Frames for OpenDisplay tags. |
| `/addon_configs/5c53de3b_esphome/` | the ESPHome add-on's configuration folder | Where **Send to ESPHome** writes a display's generated firmware configuration. Its `secrets.yaml` is read to say which names are still missing, and never written. |
| `/share/` | the Samba share, other apps | Available to the file transport (`transport: {type: file, path: /share/maverick}`). |

## Troubleshooting

**The log warns "No Home Assistant credential yet".** The app starts anyway, on
purpose — linking happens in the Web UI, so it has to be reachable first. Open
the Web UI and press **Link with Home Assistant**.

**The Web UI says it cannot link.** Home Assistant has to redirect back to the
app, so it needs an address to redirect to. Set the `base_url` option to
something reachable from your browser, for example `http://192.168.1.10:5000`,
and restart.

**Linking worked but the log says it could not be saved.** The credential is
live for this run but was not written to the app options, so it will be lost on
restart. The message names the Supervisor error; retry after a restart, or
paste a long-lived token into `home_assistant_token` instead.

**Every render is refused as a login page.** The log says
`Home Assistant redirected to the login page` or `showed the login form`.
`home_assistant_url` does not match the origin the frontend is actually served
from at that address. If Home Assistant is configured for HTTPS, use
`https://homeassistant:8123`; if it is on another port, use that port. The
token itself is checked separately and reported as `rejected the token (401)`.

**Panels never fetch anything.** Check the log line that says what `base_url`
was derived as, and whether the panel can reach that address on port 5000. Set
`base_url` explicitly if not.

**Chromium will not start.** The log shows `Could not start Chromium`. That
should not happen in this image; open an issue with the log, the machine and
the Home Assistant version.

**Adding or editing a display in the Web UI says it changed but the log warns
it could not be saved.** The change is live — the panel renders with it — but
`data/displays.yaml` could not be written, so it reverts on the app's next
restart. The log names the underlying error; it is almost always the
`/config` volume being full or the wrong owner. See
[the display store](https://github.com/ambient-home-systems/maverick-eink-dashboard/blob/main/docs/troubleshooting.md#the-display-store)
in the project's troubleshooting page.

**Where the logs are.** The app's **Log** tab. Set `log_level` to `debug` for
the browser and scheduler detail.

The project's
[troubleshooting page](https://github.com/ambient-home-systems/maverick-eink-dashboard/blob/main/docs/troubleshooting.md)
lists every message the service can print, with its cause and fix.

## What is not there yet

- **No pre-built image.** Installing builds it on your machine.
- **No custom integration.** Displays become devices through MQTT, and
  automations reach the app through those entities or a `rest_command`.

## What was verified

- The manifest (`config.yaml`) passes the app linter, `run.sh` passes
  `bash -n` and shellcheck, and the image builds on amd64 in CI, where
  `maverick --version`, `maverick panels` and a Playwright launch of the
  bundled Chromium all succeed inside the container.
- The starter `maverick.yaml` loads through the real config loader with every
  variable the service sets, and the options it reads are the ones the schema
  declares (`tests/test_app.py`).
- CI runs the built image with an `options.json` of its own and nothing in the
  environment, and checks that the loaded configuration carries those option
  values (`.github/workflows/ci.yml`, the `app` job). The Supervisor is not
  there to answer, so that run also exercises what happens when it will not:
  no broker, and a `base_url` that has to come from the option.
- Nothing on this page has been exercised on a Home Assistant OS installation:
  not the Supervisor build, not the aarch64 image, not the MQTT hand-off from
  the Mosquitto app, not the derived `base_url`. What those two calls return,
  and what grants each of them, is read from the Supervisor's own source — its
  network and services APIs and the security middleware in front of them — and
  from Home Assistant's documentation, never from a running system.
