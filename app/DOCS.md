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

   [![Open your Home Assistant instance and show the app store with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_store.svg)](https://my.home-assistant.io/redirect/supervisor_store/?repository_url=https%3A%2F%2Fgithub.com%2Fambient-home-systems%2Fmaverick-eink-dashboard)

   Or by hand: **Settings → Apps → App store**, the menu in the top right,
   **Repositories**, and paste
   `https://github.com/ambient-home-systems/maverick-eink-dashboard`.
2. Open **Maverick** in the store and install it. There is no pre-built image
   yet, so the Supervisor builds one on your machine. Expect a few minutes and
   a few hundred megabytes: Debian's Chromium is most of it.
3. On the **Configuration** tab, paste a **long-lived access token**. Create it
   under your user profile, **Security** tab. Nothing else is required.
4. **Start** the app, then open **Web UI**.

The first start writes `maverick.yaml`, with one example display, into the
app's configuration folder. Edit it to describe your panels and restart the
app; see [The configuration file](#the-configuration-file).

## Options

| Option | Required | Default | What it does |
| --- | --- | --- | --- |
| `home_assistant_url` | yes | `http://homeassistant:8123` | The origin the app loads dashboards from. Inside the app network, `homeassistant` is Home Assistant Core. Change it only if Home Assistant is served over HTTPS or on another port; it must match the origin the frontend is loaded from, scheme and port included, or every render becomes a login page and is refused. |
| `home_assistant_token` | yes | | A long-lived access token from your profile's Security tab. Rendering needs a frontend session, and the Supervisor's own token cannot open one. |
| `log_level` | yes | `info` | `debug`, `info`, `warning` or `error`. |
| `base_url` | no | derived | Where panels that pull frames (`http_pull`) should fetch from, reachable *from the panel*, for example `http://192.168.1.10:5000`. Left empty, the app uses the host's first IPv4 address on port 5000 and says so in the log. |
| `api_token` | no | | Gates the render and frame endpoints. Panels and `rest_command`s must then send it as `Authorization: Bearer`, `Access-Token` or `?token=`. |
| `mqtt_host` | no | | A broker to use instead of the Mosquitto broker app. Leave it empty and the Mosquitto app, when installed, is used automatically with the credentials it hands the Supervisor. |
| `mqtt_port` | no | `1883` | Only read when `mqtt_host` is set. |
| `mqtt_username` | no | | Only read when `mqtt_host` is set. |
| `mqtt_password` | no | | Only read when `mqtt_host` is set. |

The options do not reach the service directly. `run.sh` turns them into
environment variables, and `maverick.yaml` reads those through `${VAR}`
substitution, so the file the user edits stays the one source of truth for the
displays while the connection details come from this tab.

## The configuration file

The app writes `maverick.yaml` on its first start into its own configuration
folder, which Home Assistant exposes as `/addon_configs/<something>_maverick/`
to the **File editor**, **Studio Code Server** and **Samba share** apps. It
looks like this:

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

Edit `displays:`; leave the substituted blocks alone. Each display needs an
`id`, a `panel` from the catalogue and a `dashboard` path; everything else
defaults from the panel profile. The full key reference is
[docs/reference/configuration.md](https://github.com/ambient-home-systems/maverick-eink-dashboard/blob/main/docs/reference/configuration.md),
the panel ids are in
[docs/reference/panels.md](https://github.com/ambient-home-systems/maverick-eink-dashboard/blob/main/docs/reference/panels.md),
and there is a recipe per device path under
[docs/recipes/](https://github.com/ambient-home-systems/maverick-eink-dashboard/blob/main/docs/recipes/README.md).

Restart the app after editing. Unknown keys are errors, and the log names the
key.

## Panels that pull frames

An ESPHome board, a Kindle or a TRMNL fetches its frame from
`http://<home-assistant-host>:5000/api/displays/<id>/frame`. Port 5000 is
exposed on the host for exactly this. `base_url` is what the app tells such
devices (it appears in the generated ESPHome configuration and the TRMNL
handshake), so it has to be an address the panel can reach: set it if the
derived one is wrong, for instance when Home Assistant has more than one
network.

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
needs an adapter the process can see and is not supported inside the app.

## Files and folders

| Inside the app | Where you see it | What is there |
| --- | --- | --- |
| `/config/maverick.yaml` | `/addon_configs/<something>_maverick/maverick.yaml` | The configuration. Survives updates and reinstalls. |
| `/config/data/` | the same folder, `data/` | The frame store, `state.json`, and `debug/<id>/` when a display sets `render.debug_artifacts: true`. |
| `/media/maverick/` | Home Assistant's media folder | Frames for OpenDisplay tags. |
| `/share/` | the Samba share, other apps | Available to the file transport (`transport: {type: file, path: /share/maverick}`). |

## Troubleshooting

**The app stops at once with "home_assistant_token is empty".** Paste a token
on the Configuration tab and start it again.

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

**Where the logs are.** The app's **Log** tab. Set `log_level` to `debug` for
the browser and scheduler detail.

The project's
[troubleshooting page](https://github.com/ambient-home-systems/maverick-eink-dashboard/blob/main/docs/troubleshooting.md)
lists every message the service can print, with its cause and fix.

## What is not there yet

- **No ingress.** The UI is on port 5000 behind the **Web UI** button, not in
  the sidebar.
- **No pre-built image.** Installing builds it on your machine.
- **No custom integration.** Displays become devices through MQTT, and
  automations reach the app through those entities or a `rest_command`.

## What was verified

- The manifest (`config.yaml`) passes the app linter, `run.sh` passes
  `bash -n` and shellcheck, and the image builds on amd64 in CI, where
  `maverick --version`, `maverick panels` and a Playwright launch of the
  bundled Chromium all succeed inside the container.
- The starter `maverick.yaml` loads through the real config loader with every
  variable `run.sh` exports, and the option keys `run.sh` reads are the ones
  the schema declares (`tests/test_app.py`).
- Nothing on this page has been exercised on a Home Assistant OS installation:
  not the Supervisor build, not the aarch64 image, not the MQTT hand-off from
  the Mosquitto app, not the derived `base_url`. Those are read from the
  Supervisor's documentation and bashio's source.
