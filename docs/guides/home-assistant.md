# Connecting Maverick to Home Assistant

*Last reviewed against commit `42696c0`.*

Maverick talks to Home Assistant over its public APIs, whether it runs as the
Home Assistant app ([`app/DOCS.md`](../../app/DOCS.md)) or standalone. There is
no custom integration — see [What is not there yet](#what-is-not-there-yet) —
so everything below is done with a token, a config file and, optionally, an
MQTT broker. In the app, the token and the broker come from its options and
land in the same config file through `${VAR}` substitution, so this page
applies unchanged.

This page picks up where [the README's quick start](../../README.md#quick-start)
leaves off: you have Maverick installed and a `config.yaml`, and you want it
reading a real dashboard and controllable from inside Home Assistant.

1. [The setup UI](#the-setup-ui)
2. [The credential](#the-credential)
3. [Which dashboard URL to use](#which-dashboard-url-to-use)
4. [Making it a device: MQTT discovery](#making-it-a-device-mqtt-discovery)
5. [Without MQTT: a rest_command](#without-mqtt-a-rest_command)
6. [Rendering when data changes](#rendering-when-data-changes)
7. [OpenDisplay tags through Home Assistant's Bluetooth](#opendisplay-tags-through-home-assistants-bluetooth)
8. [What the API token protects](#what-the-api-token-protects)
9. [What is not there yet](#what-is-not-there-yet)

## The setup UI

> Status: written from the source; not yet verified against a live Home Assistant instance.

Everything below — the credential, the dashboard, MQTT discovery — can be done
by hand in `config.yaml`, but the setup UI at `/` (or **Open Web UI** in the
Home Assistant app, which embeds the same page through ingress) is where most
of it happens day to day.

**Add display** builds a display from what the running service already knows.
`GET /api/panels` fills a panel picker grouped by vendor and shows the
profile's own notes once one is chosen; `GET /api/transports` fills a
transport picker, with each transport's own option fields drawn underneath
from its `options_doc`; and the Dashboard field's suggestions come from
[the dashboard picker](#a-dashboard-path) below. Submitting posts to `POST
/api/displays` — no config file, and nothing to restart.

**Edit**, on each card, opens a drawer with every field of that display: the
schedule, the theme, the image and render settings, the lint thresholds, the
transport's own options, the wire format and the ESPHome settings, each
labelled with the same help text `docs/reference/configuration.md` is
generated from. **Preview** renders the drawer's current state — without
saving it — and shows it beside what the panel is currently showing, with the
lint findings underneath; **Save** applies it live, and **Delete** removes the
display and its stored frames, both with a confirmation first. This is
[the `Engine.render_candidate` dry-run path](../architecture.md#dry-run-previews)
and
[the runtime add/update/remove path](../architecture.md#changing-a-display-while-the-service-runs)
— nothing here needs a restart, and Chromium itself is never relaunched.

Each card also carries a **History** disclosure — the last render outcomes,
newest first, with a failed or lint-blocked one highlighted and its reason
on expand — and a **Source/Frame** toggle over the image, so you can compare
what Chromium actually captured against what the panel will show after
dithering. Both read `GET /api/displays/{id}/history` and
`GET /api/displays/{id}/screenshot.png`
([HTTP API reference](../reference/http-api.md)).

Nothing here replaces `config.yaml` for the three sections that are not
per-display: `home_assistant:`, `mqtt:` and `server:` are still edited by hand
(or, running as the app, from its Configuration tab) and need a restart to
take effect.

## The credential

> Status: written from the source; not yet verified against a live Home Assistant instance.

Maverick needs something that authenticates a **frontend session**, not just
the REST API. It does not scrape entity states and draw its own layout — it
loads your actual dashboard in headless Chromium. That is why the supervisor
token an app is handed cannot be used, and why this step exists at all
(`src/maverick/config.py`, `HomeAssistantConfig`).

There are two ways to provide one.

### Link an account (recommended)

Open Maverick's setup UI and press **Link with Home Assistant**. It sends you
to Home Assistant's own authorization page, you log in once, and it redirects
back. Nothing to copy.

This is the IndieAuth redirect flow Home Assistant's companion apps use
(`src/maverick/ha/auth.py`). Maverick's `server.base_url` becomes the
`client_id`, and `/api/auth/callback` on that same origin becomes the
`redirect_uri` — sharing an origin is what lets Home Assistant approve the
redirect without fetching anything. Home Assistant deliberately allows a local
IP address as a `client_id` host, so the app's default `base_url` works.

Two consequences worth knowing:

* `server.base_url` must be set and must be an http(s) URL, because that is
  where Home Assistant redirects back to. The setup UI says so rather than
  offering the button when it is not.
* The access token this yields lives 30 minutes, not a decade. Maverick
  refreshes it as needed, and hands the frontend the `clientId` and
  `refresh_token` too so a long render can renew its own session
  (`src/maverick/ha/auth.py`, `RefreshingToken.bundle_fields`).

Running as a Home Assistant app, the credential is written back to the app's
own options through the Supervisor, so it survives a restart. The Supervisor
lets every app change its own options without any extra permission
(`src/maverick/ha/supervisor.py`). Standalone, the callback page shows you the
two lines to put in your config file.

### Or create a long-lived token by hand

Click your user name at the bottom of the sidebar, open the **Security** tab,
scroll to **Long-lived access tokens** and create one. Home Assistant shows the
token exactly once; copy it then.

Put it in `config.yaml` under
[`home_assistant.token`](../reference/configuration.md#home_assistant). The
shipped example reads it from the environment, so the secret never lands in the
file:

```yaml
home_assistant:
  url: http://homeassistant.local:8123
  token: ${HA_TOKEN}
  verify_ssl: true
```

A **supervisor token will not do** here either, for the same reason as above.

Long-lived tokens are also bound to the instance that issued them. Point
Maverick at a different Home Assistant and the REST check fails with a 401:

```text
home assistant: FAILED — Home Assistant rejected the token (401). Long-lived access tokens are bound to the instance that issued them.
```

### The URL rule

This is the one rule worth reading twice.

`home_assistant.url` must be **the exact origin the browser will load** —
scheme, host and port. Not an equivalent address, not a different name for the
same machine, not `https` where the frontend is served over `http`.

The reason is in `src/maverick/render/dashboard.py`. The Home Assistant
frontend ignores an `Authorization` header; it reads a token bundle out of
`localStorage` under the key `hassTokens`. Maverick builds that bundle in
`build_auth_bundle` and installs it as a browser *init script*, so it is in
place before the frontend's first line of JavaScript runs rather than after a
navigation that has already bounced. The bundle carries a `hassUrl`, and the
frontend checks it against the origin it was actually loaded from. Any
mismatch and it decides the session is not for this instance and redirects to
the login screen — silently, with no error anywhere, which then screenshots as
a blank frame.

A few consequences:

* `http://homeassistant.local:8123` and `https://homeassistant.local:8123` are
  different origins. So are `http://homeassistant.local:8123` and
  `http://192.168.1.10:8123`.
* The port counts even when it is implied. If you reach the frontend at
  `https://ha.example.com` (port 443), write it without a port; if you reach it
  at `http://ha.example.com:8123`, write the port.
* A trailing slash is the one mismatch you do not have to worry about — a
  validator on `url` and `frontend_url` strips it before anything else sees it.

**`maverick check` passing is not proof the URL is right.** The check makes a
REST call to `/api/` with a bearer header, and REST does not care about the
origin the way the frontend does. A URL that is reachable but not identical to
the frontend origin passes the check and then fails every render.

### `frontend_url`, for reverse proxies

`frontend_url` exists for the case where the frontend and the API are not on
the same origin: a reverse proxy in front of Home Assistant, or container
networking where Maverick reaches the API on an internal address but the
browser should use the external one.

When it is set, it is used **only** for rendering — it becomes the base for the
dashboard URL and the `hassUrl` in the token bundle. The REST and WebSocket
clients keep using `url`. So the split is:

| Key | Used by | Must match |
| --- | --- | --- |
| `url` | The REST calls and the state-change WebSocket | Somewhere Maverick's process can reach the API |
| `frontend_url` | The browser: the dashboard URL and the token bundle | The origin the frontend is served from, exactly |

Leave `frontend_url` unset and `url` does both jobs, which is right for a plain
setup.

### `verify_ssl`

`verify_ssl: false` does two things: it turns off TLS certificate verification
on the REST client, and it launches the browser context with
`ignore_https_errors`, so a self-signed certificate does not fail the render.
It is the setting for an internally signed certificate; it is not a fix for a
wrong URL.

One gap to know about: the state-change WebSocket connection in
`src/maverick/ha/client.py` is opened without the flag, so with a self-signed
certificate over `wss://`, `schedule.on_change` triggers may still fail to
connect while everything else works. Either trust the certificate on the host
running Maverick, or point `url` at an `http://` address the process can reach
directly and use `frontend_url` for the public `https://` origin.

### The two errors you will see when it is wrong

Maverick refuses to deliver a login screen rather than shipping it — a bad
frame can sit on a battery panel for a day. `_verify_authenticated` raises one
of two `RenderError` messages, and they mean different things.

```text
[kitchen] Home Assistant redirected to the login page. The access token is missing, expired or was issued for a different URL — home_assistant.url must match the origin the token was created on, scheme and port included.
```

The browser ended up somewhere under `/auth/authorize` (or a
`/lovelace/login` path) instead of the dashboard. **This is the URL-mismatch
error.** The token bundle was installed, the frontend read it, and it rejected
it because `hassUrl` did not match the origin. Fix the origin first — compare
`home_assistant.url` (or `frontend_url`, if set) character by character with
what your browser's address bar shows. An expired or revoked token produces
the same redirect, so check that second.

```text
[kitchen] Home Assistant showed the login form. The credential was rejected: re-link the account from the setup UI, or check home_assistant.token is a long-lived access token.
```

The page loaded and stayed put, but it is rendering `ha-authorize` or
`ha-auth-flow` — the login form itself. **This is the wrong-kind-of-token
error.** The token is missing, empty, or is something other than a long-lived
access token.

Both cases also drop the browser context, so the next attempt starts from a
clean profile rather than reusing a poisoned one.

To see what Chromium actually saw, set
[`render.debug_artifacts: true`](../reference/configuration.md#displaysrender)
on the display and re-run; the raw screenshot is written to
`<data_dir>/debug/<id>/screenshot.png`.

## Which dashboard URL to use

> Status: written from the source; not yet verified against a live Home Assistant instance.

`displays[].dashboard` says what to render. It takes either a path or a fully
qualified URL, and `resolve_url` decides which by looking for a scheme.

### A dashboard path

The normal case. Write the path exactly as it appears in your browser's
address bar after the origin:

```yaml
displays:
  - id: kitchen
    panel: waveshare-7in5-mono
    dashboard: /lovelace-eink/kitchen
```

Maverick joins it onto the render origin, so that becomes
`http://homeassistant.local:8123/lovelace-eink/kitchen`. A leading slash is
optional — it is stripped before the join — and the default is `/lovelace/0`,
which is almost certainly not what you want.

Build a **separate dashboard for the panel** rather than pointing at the one
you use on a phone. Create it under **Settings → Dashboards**, give it a URL
you will recognise (`lovelace-eink` above is just a name), and design it for
the panel's size and palette. The e-ink theme Maverick injects will flatten and
restyle whatever it finds, but it cannot turn a three-column phone dashboard
into something readable at 800×480 in one ink.

Typing that path by hand means a typo is only caught after a render, which is
why the setup UI's Dashboard field offers a picker instead: with a working
Home Assistant connection, `GET /api/ha/dashboards`
(`src/maverick/ha/client.py`) lists every dashboard and view over the
WebSocket API, and the Add display form and the per-display editor turn that
into suggestions for the field, each one labelled with the dashboard's and
view's title. It is a `<datalist>`, not a closed list — an absolute URL or a
`file://` page, both below, still work by typing them in — and it fails
quietly to a plain text field with no connection.

### A full URL

Any scheme counts as absolute — the check is a general
`scheme://` pattern, not an http/https allow-list — so the value is passed
through untouched:

```yaml
dashboard: https://ha.example.com/lovelace-eink/kitchen
```

```yaml
dashboard: file:///opt/maverick/mockups/kitchen.html
```

The `file://` form is genuinely useful: it is how you preview a static mock-up
against a real panel's geometry, palette and dithering before building the
dashboard for real. Pair it with `maverick render <id> --no-deliver -o out/`
and you have a fast loop that never touches Home Assistant.

### Several dashboards on one panel

One `dashboard` per display is the common case and the shorthand. A display
that should cycle — weather in the morning, the calendar in the evening — takes
a `pages` list instead, and moves between them on command or on its own. That
is [Rotating between pages](#rotating-between-pages) below, and the keys are in
[the configuration reference](../reference/configuration.md#displayspages).

### Pages that are not Home Assistant

Two `render` keys matter as soon as the page is not a Home Assistant dashboard.

**`render.wait_for_selector`.** Unset, Maverick waits for the `home-assistant`
element, which exists on every Home Assistant page and nowhere else. On a
mock-up or a third-party page it never appears, and the render fails at the
timeout with:

```text
[kitchen] never saw 'home-assistant' at file:///opt/maverick/mockups/kitchen.html. If this is not a Home Assistant dashboard, set render.wait_for_selector to something on the page.
```

Set it to something the page really has:

```yaml
displays:
  - id: kitchen
    dashboard: file:///opt/maverick/mockups/kitchen.html
    render:
      wait_for_selector: "#board"
```

**`render.crop_to_selector`.** Capture one element instead of the whole
viewport. Useful for pulling a single card out of a dashboard onto a small tag:

```yaml
render:
  crop_to_selector: "hui-view"
```

A selector that matches nothing fails the render rather than quietly capturing
the whole page, which is deliberate: a silently wrong crop is worse than a
loud failure.

### Slow cards

Maverick first navigates waiting for `networkidle`, and falls back to
`domcontentloaded` if that never settles — a dashboard with a live camera or a
streaming graph never goes idle, and failing there would be wrong. After the
wait selector, two more knobs cover cards that are slow for their own reasons:

| Key | Default | What it does |
| --- | --- | --- |
| [`render.wait_for_images`](../reference/configuration.md#displaysrender) | `true` | Waits until every `<img>` on the page — including inside shadow roots — has decoded, then for `document.fonts.ready`. Capped at the render timeout or 15 seconds, whichever is lower. Weather and camera cards are consistently the last things to settle. |
| [`render.settle`](../reference/configuration.md#displaysrender) | `"2s"` | A flat extra wait after everything else. This is the blunt instrument for cards that fetch history or template themselves in late. |

If a card is reliably half-drawn, raise `settle` before anything else:

```yaml
render:
  settle: 6s
  timeout: 60s
```

`timeout` is the budget for both the navigation and the selector wait, so raise
it alongside `settle` rather than leaving it at its 45-second default.

## Making it a device: MQTT discovery

> Status: written from the source; not yet verified against a live Home Assistant instance.

This is the part that turns Maverick from a tool that talks to Home Assistant
into one that belongs in it. With MQTT on, every enabled display arrives as a
real Home Assistant device: an automation presses a button instead of defining
a `rest_command`, and you can see what a panel in another room is showing.

### Prerequisites

1. **A broker.** The **Mosquitto broker** add-on (**Settings → Add-ons → Add-on
   store**) is the usual choice, but any broker works — Maverick does not care
   which, and does not need to be running on the same machine as Home Assistant.
2. **The MQTT integration set up in Home Assistant**, pointed at that broker.
   Discovery is Home Assistant listening on the broker, so the integration has
   to be there for anything to appear.
3. **`mqtt.enabled: true`** in Maverick's config, with credentials for the
   broker:

```yaml
mqtt:
  enabled: true
  host: core-mosquitto
  port: 1883
  username: ${MQTT_USER:-}
  password: ${MQTT_PASSWORD:-}
  discovery_prefix: homeassistant
  base_topic: maverick
```

Two defaults to watch:

* **`host: core-mosquitto`** is the hostname of the Mosquitto add-on *on Home
  Assistant's own Docker network*. It resolves for something running inside
  that network and nowhere else. Running Maverick standalone — a laptop, a
  systemd unit on another box — set `mqtt.host` to the broker's address on your
  network. The failure is a connect timeout naming the host it tried.
* **`discovery_prefix: homeassistant`** must match the discovery prefix
  configured in Home Assistant's MQTT integration. Change it here only if you
  changed it there.

`maverick check` reports the broker state, and with MQTT off it says so
explicitly:

```text
mqtt: disabled (displays will not appear as Home Assistant entities)
```

### What appears

One device per enabled display, named after the display's `name`, carrying ten
entities: two buttons, a switch, an image of the current frame, five diagnostic
sensors and a problem binary sensor. A display with
[pages](#rotating-between-pages) gets an eleventh, a **Page** select. They are published retained, so they
survive a Home Assistant restart, and a last will marks them unavailable if
Maverick dies.

Find them under **Settings → Devices & services → MQTT**. The full table —
every entity, its `unique_id`, its category, and the state key it reads — is in
[the MQTT reference](../reference/mqtt.md#entities), along with
[the command words](../reference/mqtt.md#commands) and
[every key in the state topic](../reference/mqtt.md#the-state-topic).

Home Assistant builds each entity id from the device name and the entity name,
so a display named `Kitchen panel` gives `button.kitchen_panel_refresh`,
`switch.kitchen_panel_scheduled_renders`, `binary_sensor.kitchen_panel_problem`
and so on. Check yours in the UI before writing automations against them —
renaming the device renames the entities.

### The three things people actually do with it

#### Press **Refresh** when something changes

The button is the intended way to trigger a render from an automation. No
`rest_command`, no API token, no URL to keep in step.

```yaml
automation:
  - alias: Refresh the kitchen panel when the washing machine finishes
    triggers:
      - trigger: state
        entity_id: sensor.washing_machine_status
        to: "finished"
    actions:
      - action: button.press
        target:
          entity_id: button.kitchen_panel_refresh
```

There is a **Full refresh** button beside it
(`button.kitchen_panel_full_refresh`). It renders with `force`, which bypasses
the unchanged-frame shortcut and the lint gate and asks the panel for a
flashing full refresh that clears ghosting. Use it for a weekly clean-up, not
for every update.

Pressing Refresh more often than the dashboard changes is close to free: if the
new frame is byte-identical to the last one, delivery is skipped and the panel
never refreshes. The render still costs a few seconds of Chromium.

#### Pause the bedroom panel at night

The **Scheduled renders** switch pauses a display's timeline at runtime,
without editing the config file. A full refresh flashes the whole panel black
and white several times, which is not what you want at 3am.

```yaml
automation:
  - alias: Pause the bedroom panel overnight
    triggers:
      - trigger: time
        at: "23:00:00"
    actions:
      - action: switch.turn_off
        target:
          entity_id: switch.bedroom_panel_scheduled_renders

  - alias: Resume the bedroom panel in the morning
    triggers:
      - trigger: time
        at: "06:30:00"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.bedroom_panel_scheduled_renders
```

The switch sends `schedule_off` and `schedule_on`, and its state comes back
from the display's state topic, so the switch and the service stay in step.

It gates the same two triggers `quiet_hours` does — the schedule and
`on_change` — and nothing else: a button press, an API call or a startup render
still runs while it is off. It also lives in memory rather than in the config
file, so a restart resumes the schedule. If you want the pause to survive a
restart, use [`schedule.quiet_hours`](#quiet-hours) instead and keep the switch
for changing your mind.

#### Alert on the problem binary sensor

`binary_sensor.<name>_problem` goes on when a display has consecutive failures
— the last render failed and none has succeeded since. It is the entity to
alert on, because the failure mode that matters is silent: a panel that stopped
updating looks exactly like a panel whose data has not changed.

```yaml
automation:
  - alias: Tell me when a panel stops rendering
    triggers:
      - trigger: state
        entity_id: binary_sensor.kitchen_panel_problem
        to: "on"
        for:
          minutes: 15
    actions:
      - action: notify.persistent_notification
        data:
          title: Kitchen panel has stopped updating
          message: >-
            Status is {{ states('sensor.kitchen_panel_status') }}, last render
            {{ relative_time(states.sensor.kitchen_panel_last_render.last_changed) }}
            ago.
```

The `for:` matters. A single failed render trips the sensor, and a transient
one — Home Assistant restarting mid-screenshot — clears on the next scheduled
attempt. Fifteen minutes on a five-minute schedule means two recoveries before
you hear about it.

`sensor.<name>_last_render` is the other half of the picture: it is a
`timestamp` sensor that only advances on a *successful* render, so on a panel
whose data rarely changes it is the honest answer to "is this thing alive?".

## Without MQTT: a `rest_command`

> Status: written from the source; not yet verified against a live Home Assistant instance.

No broker, or not ready to run one? Home Assistant can call Maverick's HTTP API
directly. Add a `rest_command` to `configuration.yaml`:

```yaml
rest_command:
  maverick_render:
    url: "http://maverick.local:5000/api/displays/{{ display }}/render"
    method: post
```

If [`server.api_token`](../reference/configuration.md#server) is set, the render
routes require it — as a bearer token, an `Access-Token` header, or a `?token=`
query parameter. The bearer header is the one to use here:

```yaml
rest_command:
  maverick_render:
    url: "http://maverick.local:5000/api/displays/{{ display }}/render"
    method: post
    headers:
      authorization: "Bearer YOUR_API_TOKEN"
```

Keep the token out of `configuration.yaml` by putting it in `secrets.yaml` and
writing `authorization: !secret maverick_authorization` instead, with the value
`"Bearer your-api-token"`.

Leave `server.api_token` empty and the endpoint is unauthenticated, in which
case drop the `headers:` block entirely.

Call it from an automation exactly like the button press:

```yaml
automation:
  - alias: Refresh the kitchen panel when the washing machine finishes
    triggers:
      - trigger: state
        entity_id: sensor.washing_machine_status
        to: "finished"
    actions:
      - action: rest_command.maverick_render
        data:
          display: kitchen
```

Add `?force=true` to the URL for the equivalent of the Full refresh button, and
see [the HTTP API reference](../reference/http-api.md) for the response body
and for `POST /api/render`, which renders every enabled display.

### What you give up

**Nothing appears in Home Assistant as an entity.** There is no device, no
button to press from a dashboard or a voice assistant, no image of what the
panel is showing, no `last render` timestamp, and — most significantly — no
problem binary sensor to alert on. A `rest_command` is fire-and-forget from
Home Assistant's side: it will not tell you that the render failed, only that
the HTTP call did or did not return an error, and the automation editor has
nothing to offer you in a picker.

Knowing whether a panel is healthy means reading Maverick's log or polling
`GET /api/displays` yourself.

Maverick's Home Assistant client does carry a `set_state` helper that pushes a
Maverick-owned entity into Home Assistant over the REST API with no broker
involved — but nothing calls it today, and its docstring says why it would not
be enough anyway: states created that way **do not survive a Home Assistant
restart**. They are not backed by anything; Home Assistant forgets them and the
entity comes back as unknown. That is the reason MQTT discovery is the
preferred path — retained discovery messages on the broker are what make the
device real across restarts on both sides.

## Rendering when data changes

> Status: written from the source; not yet verified against a live Home Assistant instance.

A panel on a five-minute timer is refreshing all night for nobody, and is still
showing stale data for up to five minutes after the front door opens. The fix
is to follow the data instead of a clock.

### `on_change`

List the entity ids a display depends on and Maverick re-renders it when any of
them changes state:

```yaml
displays:
  - id: hallway-tag
    panel: opendisplay-solum-2in9-bwr
    dashboard: /lovelace-eink/tag
    schedule:
      on_change:
        - binary_sensor.front_door
        - person.sam
      debounce: 30s
```

With no `every` and no `cron` alongside it, that display renders only when
something it shows has actually changed.

Under the bonnet this is a single WebSocket subscription to Home Assistant's
`state_changed` event, shared across every display, reconnecting with backoff —
Home Assistant restarts on every config reload, and treating a dropped socket
as fatal would mean a panel silently stopping until Maverick was restarted.
Polling would mean waking the whole render stack every few seconds to ask a
question whose answer is almost always no.

It needs a Home Assistant connection. Without a token there is no client, and
the scheduler logs that state triggers are inactive rather than failing:

```text
on_change is configured for 2 entities but Home Assistant is not connected; state triggers are inactive
```

### Attribute-only changes are ignored

Home Assistant fires `state_changed` for attribute updates too, and Maverick
drops those: if the old and new `state` strings are equal, nothing happens.

This is not a detail — it is what makes `on_change` usable. A media player
updates its position attribute every few seconds while its state stays
`playing`. A weather entity rewrites its whole forecast attribute on every
poll. Acting on those would mean a panel refresh per attribute tick, and an
e-ink refresh is the expensive thing in this system, not the render.

The flip side: if what your dashboard shows lives in an attribute rather than
in the state — a forecast, a media title, a timer's remaining time — changing
it will not trigger a render. Give that display an `every` or a `cron` as well.

Note that `unavailable` and `unknown` *are* states, so an entity dropping out
counts as a change and will trigger a render.

### `debounce`

`debounce` (default `10s`, per display) coalesces a burst into one render. Each
change restarts the timer, and the render fires once the entity has been quiet
for that long — a thermostat reporting every few seconds would otherwise queue
a render per reading, and each one costs a panel refresh.

Because the timer restarts, an entity that changes *continuously* at intervals
shorter than `debounce` never reaches the render at all — the timer is pushed
out again before it ever fires. So keep `debounce` shorter than the gaps you
expect between updates, and give a genuinely chatty entity a timer instead of
an `on_change` entry.

### Quiet hours

```yaml
schedule:
  every: 5m
  quiet_hours: "23:00-06:30"
```

The window is `HH:MM-HH:MM` in the **server's local time**, and it may wrap
midnight — `23:00-06:30` means late evening or early morning, which is what you
want in a bedroom.

### What quiet hours and the pause switch actually gate

Both the switch and `quiet_hours` are checked in one place, against the
*trigger* that started the render, and they gate exactly two of them:

| Trigger | What starts it | Respects quiet hours and the switch? |
| --- | --- | --- |
| `schedule` | `every` or `cron` coming round | **Yes** |
| `state` | An `on_change` entity changing | **Yes** |
| `button` | The Refresh or Full refresh button over MQTT | No |
| `page` | A page change: the Page select, `POST /api/displays/{id}/page`, or rotation moving on | **Only rotation**, which happens inside a `schedule` tick |
| `api` | `POST /api/displays/{id}/render` | No |
| `cli` | `maverick render <id>` | No |
| `manual` | The engine's default, for a render nothing else labelled | No |
| `startup` | `schedule.render_on_start`, on by default | No |

The rule behind the table: quiet hours and the switch exist to stop a panel
refreshing *on its own* while you are asleep. Something you asked for
explicitly is not that, so it runs. Press Refresh at 2am and the panel
refreshes at 2am.

One consequence worth planning for: `render_on_start` defaults to `true`, so
restarting Maverick during quiet hours renders immediately anyway. If that
matters in a bedroom, set `render_on_start: false` on that display.

### Rotating between pages

The other way a panel stops being one fixed view: give the display several
dashboards and let it move between them.

```yaml
displays:
  - id: kitchen
    panel: waveshare-7in5-mono
    pages:
      - dashboard: /lovelace-eink/overview
        dwell: 30m
      - dashboard: /lovelace-eink/calendar
        name: Week ahead
        dwell: 10m
    rotate: true
    schedule:
      every: 5m
```

`pages` replaces `dashboard` — a display sets one or the other, and both
together fails the load with an error naming the display
(`src/maverick/config.py`). Each page may carry a `name`; leave it out and one
is derived from the last segment of the path, so `/lovelace-eink/overview`
becomes "Overview".

**`rotate` rides on the schedule.** The page advances on a *scheduled* render,
and only once the current page's `dwell` has elapsed
(`RenderScheduler._rotate`, `src/maverick/scheduling/scheduler.py`). With the
config above the panel renders every five minutes and changes page every thirty:
the overview holds for six renders, the calendar for two, then round again. A
page with no `dwell` changes at every scheduled render, and a `dwell` shorter
than `every` means the same thing — a page cannot change faster than the
schedule driving it. Quiet hours stop rotation as well as rendering, because
they stop the scheduled render that would have done both.

**Anything can change the page, not just the clock.** With MQTT on, the device
gains a **Page** select whose options are the page names, and an automation can
set it like any other select:

```yaml
action: select.select_option
target:
  entity_id: select.kitchen_panel_page
data:
  option: Week ahead
```

`select.select_next` and `select.select_previous` work too, and reach the same
place as publishing `next_page` or `previous_page` to the display's command
topic ([the command words](../reference/mqtt.md#commands)). Without a broker,
`POST /api/displays/kitchen/page` takes `{"index": 1}`, `{"name": "Week
ahead"}` or `{"step": 1}`
([HTTP API](../reference/http-api.md#post-apidisplaysdisplay_idpage)). Either
way the page change is remembered across a restart, and the render it triggers
is labelled `page` in the state topic and the render history.

Leave `rotate` off for a display you only ever move by hand: the pages are
still there, and nothing advances them on its own.

## OpenDisplay tags through Home Assistant's Bluetooth

> Status: written from the source; not yet verified against a live Home Assistant instance.

OpenDisplay e-paper tags — reflashed electronic shelf labels, mostly — are
BLE-only. Maverick can reach them two ways, and the difference matters more
than it looks.

### `mode: ha`: let Home Assistant do the Bluetooth

Maverick hands the finished frame to Home Assistant's own
`opendisplay.upload_image` action and Home Assistant delivers it over whatever
Bluetooth it has — **including ESPHome Bluetooth proxies**. That is the point:
a proxy in the room with the tag gives coverage a server in a cupboard cannot
match, and Maverick itself needs no Bluetooth hardware at all.

```yaml
displays:
  - id: hallway-tag
    panel: opendisplay-solum-2in9-bwr
    dashboard: /lovelace-eink/tag
    transport:
      type: opendisplay
      mode: ha
      device_id: 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
      media_dir: /media/maverick
      media_root: /media
```

The default is actually `mode: auto`, which resolves to `ha` when Home
Assistant is connected and `ble` otherwise. Writing `ha` explicitly means a
missing token gives you a clear failure rather than a silent fallback to a
Bluetooth adapter that is not there.

**Prerequisite: the OpenDisplay integration must be set up in Home Assistant.**
It is what provides the `opendisplay.upload_image` action; without it every
delivery fails with the action name in the message.

**`device_id` is the device registry id.** Not the entity id, not the MAC. Find
it under **Settings → Devices & services → Devices**, open the tag, and read
the id out of the browser's address bar — the URL ends
`/config/devices/device/<id>`, and that trailing value is what goes in the
config. Get it wrong and the upload fails with:

```text
opendisplay.upload_image failed: … Check the OpenDisplay integration is set up and device_id is correct — it is the device registry id, not the entity id or the MAC.
```

### The media path is the part that catches people

The Home Assistant action does not take image bytes — it takes a media source
id. So Maverick writes the frame to disk and passes Home Assistant a pointer to
it:

1. Write `<media_dir>/<display id>.png` (`/media/maverick/hallway-tag.png` by
   default), creating the directory if needed.
2. Work out that file's path **relative to `media_root`** (`/media` by
   default) — here, `maverick/hallway-tag.png`.
3. Prefix it with `media_source_prefix`
   (`media-source://media_source/local` by default) and call
   `opendisplay.upload_image` with the result as the image's
   `media_content_id`.

Two requirements fall out of that, and both are about **the same path meaning
the same thing to both processes**:

* **`media_dir` must sit inside `media_root`.** Home Assistant can only read
  images from its media folder, so a directory outside it cannot be named as a
  media source at all. Maverick checks and refuses rather than calling the
  action and getting a confusing error back:

  ```text
  media_dir /srv/frames is not inside media_root /media; Home Assistant can only read images from its media folder.
  ```

* **Home Assistant must see that directory at that path.** Maverick writing
  `/media/maverick/hallway-tag.png` is only useful if `/media/maverick` is the
  same directory Home Assistant serves as `local` media. On one machine, a bind
  mount or a shared volume. On two, a network share mounted at the same path on
  the Maverick side. If Maverick cannot write there at all you get:

  ```text
  cannot write to /media/maverick (…). The add-on needs the 'media:rw' mapping, or set transport.media_dir to a shared path.
  ```

  (The `media:rw` mapping is app wording: the Home Assistant app maps `/media`
  read-write, so this just works there. Standalone, read it as "give the
  process write access to the shared media directory".)

`rotation` is passed through to the action when set, and it is the *tag's own*
rotation — not `displays[].rotation`, which Maverick has already applied to the
image before writing it.

### `mode: ble`: talk to the tag from this machine

```yaml
transport:
  type: opendisplay
  mode: ble
  mac: "AA:BB:CC:DD:EE:FF"
```

This uses `py-opendisplay` to drive the tag over GATT directly. It needs:

* **The `opendisplay` extra**, which is not installed by default:

  ```bash
  pip install "maverick-eink-dashboard[opendisplay] @ git+https://github.com/ambient-home-systems/maverick-eink-dashboard"
  ```

* **A real Bluetooth adapter the process can see**, and the tag in range of
  *that* adapter — no proxies, no Home Assistant.
* **`mac` or `device_name`.** Run `maverick scan` to list the tags in range
  with both:

  ```bash
  maverick scan
  ```

The trade is coverage against independence: `ble` works with no Home Assistant
at all and is the right mode for setup and diagnostics, but it only reaches
what one adapter can hear. `ha` reaches anything any of your proxies can hear.

Either way, Maverick sends the frame it has already quantised to the panel's
palette, with the library's own dithering switched off — re-dithering an
already-exact image would undo the text-preserving work the pipeline just did.

## What the API token protects

> Status: written from the source; not yet verified against a live Home Assistant instance.

[`server.api_token`](../reference/configuration.md#server) is Maverick's own
token and has nothing to do with the Home Assistant credential above. Leave it
empty and every route is open. Set it and it gates the published port: the
render and frame endpoints, the generated ESPHome configuration, the preview
PNGs and the setup UI at `/` (`src/maverick/server/api.py`). The UI asks for
the token when it needs one and keeps it for that browser tab, so its buttons
work without `?token=` in the address bar (`src/maverick/server/ui.py`).

What it does not gate is everything that has to be reachable without
credentials: `/health`, the panel and transport catalogues, the two TRMNL
handshake routes, and `/api/auth/callback`, which Home Assistant redirects a
browser to and which is authenticated by its own single-use nonce instead
(see [the HTTP API reference](../reference/http-api.md#authentication)).
Opening the app's UI from inside Home Assistant is exempt as well: those
requests arrive over ingress, which the Supervisor proxies from a known address
on its own network once Home Assistant has authenticated the user
(`src/maverick/ha/supervisor.py`). One knock-on is worth knowing before you set
it: each display's `image` entity then receives its frame over MQTT rather than
as a URL, because Home Assistant fetches an image URL with no credentials
(`src/maverick/ha/discovery.py`).

None of this makes Maverick safe to expose to the internet. The token travels
in the clear over plain HTTP, so it is a gate on a trusted network, not a
perimeter.

## What is not there yet

These pieces are described in [docs/roadmap.md](../roadmap.md) as proposals,
and none of them exists today:

* **No sidebar entry.** Ingress is enabled (`app/config.yaml`), so **Open Web
  UI** on the app's page opens the setup UI embedded in Home Assistant on any
  connection. It is not a sidebar panel, and port 5000 stays published for
  panels that pull frames — and as the way in if ingress is unavailable.
* **No custom integration.** No config flow, no HACS listing, no UI setup. The
  configuration is the YAML file described here.
* **No `maverick.*` actions.** Automations reach Maverick through the MQTT
  entities or a `rest_command`, not through native Home Assistant actions.
* **No control card.** A display's pages, its refresh and its status are all
  reachable — the entities above, and
  [Rotating between pages](#rotating-between-pages) — but the tile feature and
  the card that would gather them into one place on a dashboard are not built.

## Where to go next

* [Configuration reference](../reference/configuration.md) — every key, its
  type, default and effect.
* [MQTT reference](../reference/mqtt.md) — every topic, discovery payload,
  command word and state key.
* [HTTP API reference](../reference/http-api.md) — every route, including the
  pull protocol a battery panel uses.
* [Troubleshooting](../troubleshooting.md) and
  [the README's list of first failures](../../README.md#troubleshooting).
