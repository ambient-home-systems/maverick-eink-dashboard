# Troubleshooting

*Last reviewed against commit `a964f6c`.*

Every user-facing failure message Maverick can produce, grouped in the order
you meet them: loading the config, connecting to Home Assistant, rendering,
the lint gate, delivery, MQTT and discovery, and the scheduler. The last three
sections cover the failures worth pricing in before you buy the hardware, and
how to read and reset the state Maverick keeps on disk.

Every message below is quoted **verbatim from the source**, including its
`{placeholder}` or `%s` markers exactly as written — substitute your own
values when matching it against what you see.

1. [Loading the config](#loading-the-config)
2. [Connecting to Home Assistant](#connecting-to-home-assistant)
3. [Rendering](#rendering)
4. [The lint gate](#the-lint-gate)
5. [Delivery](#delivery)
6. [MQTT and discovery](#mqtt-and-discovery)
7. [The scheduler](#the-scheduler)
8. [Changing a display while it runs](#changing-a-display-while-it-runs)
9. [Risks that hurt most](#risks-that-hurt-most)
10. [Reading the state](#reading-the-state)

## Where a message ends up

Four surfaces carry these messages, and each entry below says which apply:

* **CLI** — printed by the `maverick` subcommand that hit the failure.
* **log** — written to the service log at the given level (`maverick serve`'s
  stdout, or the add-on log).
* **state** — the failure lands in that display's `state.last_error`
  (`src/maverick/engine.py`, `DisplayState`), from where it reaches the MQTT
  state topic's `error` field and the `reason`/`delivery` fields of the HTTP
  API — see [the state topic](reference/mqtt.md#the-state-topic). The
  `sensor.<name>_status` entity only ever shows `ok`, `unchanged` or `error`;
  it never carries the message text itself, and there is no dedicated entity
  for it — read the raw state topic, or template one yourself.
* **UI** — the red error line on that display's card in the setup UI
  (`src/maverick/server/static/app.js`, which draws the cards from
  `GET /api/displays`), truncated to 300 characters.

A **config-load** failure (anything in the first section) happens before the
engine exists, so it is CLI-only: `maverick` prints it and exits — a
`ConfigError` as `config error: <message>` with exit code 2, and any other
exception as `error: <Type>: <message>` with exit code 1 (set
`MAVERICK_DEBUG=1` for a traceback). Everything from *rendering* onward is a
**runtime** failure and reaches state, UI and MQTT as well as the log.

---

## Loading the config

Config errors stop `maverick` before it starts serving anything. All of them
are `ConfigError` (`src/maverick/config.py:35`) except the two path checks in
`_find_config`, which raise the same class from `src/maverick/cli.py`.

### No config file found

```text
config file not found: {path}
```

**Cause:** `-c`/`--config` named a path that does not exist.
**Fix:** check the path, or drop `-c` and let Maverick look in the default
locations.
**Command:** `maverick -c <path> ...`

```text
No config file found. Looked in: {paths}. Run `maverick init > config.yaml` to create one.
```

**Cause:** no `-c` given, and none of `config.yaml`, `/config/maverick.yaml`
or `~/.config/maverick/config.yaml` exist (`DEFAULT_CONFIG_PATHS` in
`src/maverick/cli.py`).
**Fix:** `maverick init > config.yaml` writes a starter file in the current
directory; edit it and re-run, or pass `-c` explicitly. Inside the Home
Assistant app it means the starter config was never written to
`/config/maverick.yaml`, which `app/run.sh` does on the first start — start the
app once and read its log.

```text
Config file not found: {path}
```

**Cause:** the same check, run a second time inside `load_config` (note the
capital "C" — this is a different call site to the one above, reached once a
path has already been chosen). In practice this fires only if the file is
deleted between `_find_config` finding it and `load_config` reading it.
**Fix:** as above.

### The Home Assistant app's options

Under the app, the connection settings come from the Configuration tab rather
than from `maverick.yaml`. The service reads them itself, from
`/data/options.json`, and sets the environment variables the config file
substitutes — `HA_URL`, `MQTT_HOST` and the rest (`load_app_options` in
`src/maverick/ha/options.py`, called by `_load` in `src/maverick/cli.py`
whenever a `SUPERVISOR_TOKEN` is in the environment). Everything in this
section is a **log** message written while that happens, and none of them stops
the app: what several of them describe is fixed in the app's own web UI, which
has to be running to be reached.

```text
Could not read the app's options from %s (%s); every option will be treated as unset.
```

**Cause:** `/data/options.json` could not be read, or does not hold a JSON
object. The Supervisor writes that file for every app, and Maverick only reads
it when it is there (`_load` in `src/maverick/cli.py` checks first), so inside
the app this means something went wrong outside Maverick — a file that vanished
mid-start, or one truncated by a full disk.
**Fix:** none inside the app — it starts with every option unset, which is the
state a fresh install is in, and the Configuration tab is read again on the
next start. Elsewhere, put the settings in `maverick.yaml` or the environment
instead.

```text
No Home Assistant credential yet, so rendering will fail.
Open this app's web UI and press 'Link with Home Assistant'.
You can instead paste a long-lived access token (your profile, Security tab) into the home_assistant_token option.
```

**Cause:** neither `home_assistant_token` nor `home_assistant_refresh_token` is
set, which is how a freshly installed app arrives. Starting anyway is
deliberate: linking happens in the web UI, so refusing to start would put the
fix out of reach.
**Fix:** what the lines say. The credential itself, and what each kind is good
for, is [The credential](#the-credential) below.

```text
base_url is not set and the host address could not be read;
panels that pull frames will not know where to fetch from.
```

**Cause:** the `base_url` option is empty and the host's own address could not
be read. Maverick asks the Supervisor for `GET /network/info` and takes the
first IPv4 address it lists (`_host_ipv4` in
`src/maverick/ha/options.py`); this is what an app with no `hassio_api`
permission, or a Supervisor that refused the call, leaves behind. The variable
is left empty rather than guessed, because a panel told to fetch from an
address that is not the host's simply stops refreshing.
**Fix:** set **Base URL for panels** (`base_url`) on the Configuration tab to
an address the panel can reach, for example `http://192.168.1.10:5000`. The
line above it in the log says why the lookup failed.

Five more lines are logged at **info** on the same path, and they are the trail
worth reading when a panel or a broker is not where you expected:

| Message | Means |
|---|---|
| `base_url is not set; panels will be told to fetch from %s` | The address above was derived successfully; this is the one panels get. |
| `MQTT: using the broker from the app options (%s:%s)` | `mqtt_host` is set, so the Mosquitto app is not consulted at all. |
| `MQTT: using the Mosquitto broker app (%s:%s); displays will appear as devices` | The broker app's own host and credentials came back from `GET /services/mqtt`. |
| `MQTT: no broker configured and the Mosquitto broker app is not installed;` | Neither source answered. MQTT is off, displays do not appear as Home Assistant devices, and rendering is unaffected. |
| `The Supervisor did not answer GET %s (%s).` | One of those two Supervisor calls failed, with its status. `400 Service not enabled` on `/services/mqtt` is the ordinary "Mosquitto is not installed" case; a `403` means a permission the app is missing (`hassio_api` for `/network/info`, `services: mqtt:want` for `/services/mqtt`, both in `app/config.yaml`). |

### The YAML itself is wrong

```text
{path} must contain a YAML mapping at the top level
```

**Cause:** the file parses as YAML but its top level is a list or a scalar,
not a mapping — usually a stray leading `-` or a value pasted without its key.
**Fix:** the file must open with `home_assistant:`, `displays:`, or similar
top-level keys.

### Environment substitution

```text
{value!r} is not a duration (try '30s', '5m', '1h')
```

**Cause:** a field that accepts a duration (`render.settle`, `render.timeout`,
`schedule.debounce`, `schedule.every`) got a string `parse_duration` cannot
parse.
**Fix:** use `<number><unit>` — `ms`, `s`, `m`, `h` or `d` — or a bare number
of seconds.

```text
Environment variable {m.group(1)} is referenced in the config but not set (use ${{{m.group(1)}:-default}} to make it optional).
```

**Cause:** the config uses `${VAR}` and `VAR` is not set in the environment
Maverick runs under.
**Fix:** set the variable (in the add-on's environment, your shell, or a
`.env` your process manager loads), or change the reference to
`${VAR:-default}` so it is optional.

### Field-level validation

| Message | Cause | Fix |
|---|---|---|
| `black_level must be below white_level` | `image.black_level` ≥ `image.white_level` | Lower `black_level` or raise `white_level`; they define a level-stretch range. |
| `set either 'every' or 'cron', not both` | both `schedule.every` and `schedule.cron` set on a display | Remove one — they are mutually exclusive schedule kinds. |
| `quiet_hours must look like '23:00-06:30'` | `schedule.quiet_hours` does not match `H:MM-H:MM` | Use 24-hour `HH:MM-HH:MM`, wrapping midnight is fine (`23:00-06:30`). |
| `display id {v!r} must be lowercase alphanumeric with - or _ (it becomes a URL path and an MQTT topic)` | `displays[].id` fails the slug pattern | Use lowercase letters, digits, `-` and `_`, starting with a letter or digit. |
| `rotation must be 0, 90, 180 or 270` | `displays[].rotation` set to anything else | One of the four right-angle values only. |
| `duplicate display id {display.id!r}` | two entries under `displays:` share an `id` | Rename one. |

Also validated at load time but raised as `KeyError`, not `ConfigError`, so it
is printed by the generic handler as `error: KeyError: ...`:
`displays[].panel` naming a panel the catalogue does not know
(`src/maverick/devices/profiles.py`) — `maverick panels` lists every valid
value, with a "did you mean" suggestion in the message itself.

Every row above applies to a display in the store file as much as to one in the
config file: `DisplayStore.load` validates through `Config`
(`src/maverick/store.py`), so the same validators produce the same messages —
including `duplicate display id`, which is the one you can meet by hand-editing
a store Maverick wrote.

### The display store

Displays live in `<data_dir>/displays.yaml` — or wherever `displays_file`
points — which Maverick writes for itself (`src/maverick/store.py`). It is read
on every load, so a file that is not a display store stops the service the way a
broken config file does.

```text
{self.path} must contain a YAML mapping at the top level
```

**Cause:** the store parses as YAML but its top level is not a mapping. It has
to be `version:` and `displays:`.
**Fix:** delete the file and let the next save write it again, or restore the
`version`/`displays` shape by hand.

```text
{self.path} is a display store of version {version!r}, and this Maverick reads version {VERSION}. Upgrade Maverick, or move the file aside and let it be written again.
```

**Cause:** the `version:` key is not `1` — a store written by a newer Maverick
than the one reading it, which is the downgrade case.
**Fix:** run the newer version, or move the file aside; the displays in it are
not readable by this one, and guessing at them is worse than saying so.

```text
{self.path}: 'displays' must be a list, one entry per panel
```

**Cause:** `displays:` in the store holds a mapping or a scalar.
**Fix:** one `- id: ...` entry per display, as in the config file.

```text
displays are listed in both %s and %s; the store wins. The displays: list in %s is ignored and can be deleted.
```

**Cause (log, warning):** the store exists *and* the config file still has a
`displays:` list. Two sources of truth for the same panels is the thing this
file exists to avoid, so the store — the one Maverick writes — is used and the
list is not.
**Fix:** delete the `displays:` list from the config file. Nothing is lost: it
was imported into the store the first time it was loaded, and the warning names
both paths so you can compare them before deleting.

```text
could not write the display store %s: %s. The displays in %s are still rendered, but they cannot be changed until the store can be written.
```

**Cause (log, warning):** the first load could not create the store — a
read-only `data_dir`, a full disk, or the wrong owner. The displays from the
config file are used for this run, as they always were.
**Fix:** make `data_dir` writable by the user Maverick runs as, or point
`displays_file` somewhere that is. Until then the service renders and delivers
normally; it is only the store, and so changing a display, that is lost.

A successful import says so once, at info:

```text
imported %d display(s) from %s into %s; they are managed in the setup UI from now on, and the displays: list in %s can be deleted
```

`maverick check` prints the same answer without starting anything:
`config: 2 display(s) from /config/data/displays.yaml (the display store)`.

---

## Connecting to Home Assistant

`HomeAssistantError` (`src/maverick/ha/client.py:30`) is raised by
`HomeAssistantClient` and always caught by its caller — it never crashes the
process, but it is why rendering then fails.

### The credential

Maverick accepts two, and both authenticate a browser session rather than just
the REST API (`src/maverick/config.py`, `HomeAssistantConfig`): a long-lived
access token in `home_assistant.token`, or a linked account
(`home_assistant.refresh_token` plus `home_assistant.client_id`) obtained by
the setup UI's **Link with Home Assistant** button.

```text
No Home Assistant credential configured. Open Maverick's setup UI and use 'Link with Home Assistant', or create a long-lived access token under your profile -> Security and set home_assistant.token.
```

**Where:** raised inside `HomeAssistantClient.check()`. Both current callers
(`maverick check` and `Engine.start()`) test for a credential first and skip
calling `check()` when there is none, printing the shorter warning below
instead — so you are more likely to meet those. This message is the guard
inside `check()` itself, for any future or scripted caller.
**Fix:** open the setup UI and press **Link with Home Assistant**, or create a
long-lived access token (your Home Assistant profile → Security → Long-lived
access tokens) and set
[`home_assistant.token`](reference/configuration.md#home_assistant).

```text
No Home Assistant credential configured. Open Maverick's setup UI and use 'Link with Home Assistant', or set home_assistant.token to a long-lived access token.
```

**Where:** raised by `HomeAssistantClient` when a REST or WebSocket call needs
a token and none is configured. Same fix as above.

```text
No Home Assistant credential configured. Open Maverick's setup UI and use 'Link with Home Assistant', or set home_assistant.token.
```

**Where:** `RenderError` from `build_auth_bundle()`, so it names the display it
failed for. Same fix as above.

```text
home assistant: no token configured — rendering will fail
```

**Where:** CLI, printed by `maverick check`.

```text
no Home Assistant credential configured — dashboard rendering will fail until an account is linked from the setup UI or home_assistant.token is set
```

**Where:** log (warning), from `Engine.start()` at every startup while no
credential is configured. **Surfaces:** log only. The service still starts, on
purpose: the setup UI is how you link an account, and it has to be reachable
to do that.

```text
No Home Assistant credential to apply.
```

**Where:** raised by `Engine.relink()`, which the setup UI calls after a
successful link. Reaching it means the exchange produced no usable credential
— retry the link.

```text
Home Assistant rejected the token (401). Long-lived access tokens are bound to the instance that issued them.
```

**Cause:** the token is valid syntax but this instance did not issue it — a
token copied from a different Home Assistant, or a supervisor token.
**Fix:** create a new token on the exact instance `home_assistant.url` points
at.

```text
Home Assistant rejected the token (401). The linked account may have been revoked under profile -> Security; link it again from Maverick's setup UI.
```

**Cause:** the same 401, but against a linked account rather than a pasted
token. Refresh tokens appear in Home Assistant's profile page alongside
long-lived tokens and can be deleted there.
**Fix:** press **Link with Home Assistant** again.

```text
Home Assistant rejected the token request (400): Invalid client id
```

**Cause:** `home_assistant.client_id` is not a URL Home Assistant's IndieAuth
validator accepts, so the refresh that every render begins with is refused
before the refresh token is even looked at
(`homeassistant/components/auth/indieauth.py`, `verify_client_id`; a client id
that is merely the *wrong* one answers `invalid_request` with no description
instead). It must be the same http(s) origin the link was made with — the one
`/api/auth/status` reports, derived from `server.base_url` by
`client_id_for` (`src/maverick/ha/auth.py:72-85`). Under the Home Assistant
app before 0.2.4 it could be the literal string `null`, which is what an
unlinked install looked like; updating the app fixes that case.
**Fix:** press **Link with Home Assistant** again, which rewrites
`client_id` and `refresh_token` together — they only work as a pair. Editing
one by hand is what breaks them.

### Reachability

```text
Cannot reach Home Assistant at {self._config.url}: {exc}
```

**Cause:** the REST request itself failed — wrong host, firewalled port, TLS
handshake failure, DNS.
**Fix:** confirm `home_assistant.url` resolves and is reachable from wherever
Maverick runs (a container's `localhost` is not the host's), and that
`home_assistant.verify_ssl` matches whether the certificate is trusted.

```text
Home Assistant check failed: %s
```

**Where:** log (error), from `Engine.start()` — wraps whichever
`HomeAssistantError` above `check()` raised. **Surfaces:** log only; the
server keeps starting and serves without a working Home Assistant connection,
so renders fail until this is fixed.

### Service calls and the websocket

```text
{domain}.{service} failed ({response.status_code}): {response.text[:400]}
```

**Cause:** `call_service` posted to `/api/services/<domain>/<service>` and got
a 4xx/5xx back — most often `opendisplay.upload_image` when the OpenDisplay
integration or its `device_id` is wrong (it reaches you wrapped inside the
[OpenDisplay delivery failure](#opendisplay-opendisplay) message, not on its own).
**Fix:** check the domain/service exists and its `device_id` is correct.

```text
Home Assistant WebSocket command '{message_type}' failed: {error.get('message', result)}
```

**Cause:** `list_dashboards()` — the Dashboard field's picker, behind
`GET /api/ha/dashboards` — sent `lovelace/dashboards/list` or `lovelace/config`
over the websocket and Home Assistant answered `success: false`. A single
dashboard's own `lovelace/config` failing is caught and that dashboard still
appears with an empty `views` list (a YAML-mode dashboard, most often); this
message is what reaches you when `lovelace/dashboards/list` itself fails,
since then there is nothing to list at all.
**Surfaces:** as a 503 from `GET /api/ha/dashboards`, whose detail is this
message; the Dashboard field falls back to a plain text input rather than
showing an error (`src/maverick/server/static/app.js`).
**Fix:** read `error.message` in the 503 body; most often the linked account
lacks the access dashboard management needs.

```text
unexpected WebSocket greeting: {greeting}
```

```text
WebSocket auth failed: {result.get('message', result)}
```

**Cause (both):** the `state_changed` subscription behind `schedule.on_change`
did not authenticate — a proxy stripping the WebSocket upgrade, or the token
being rejected at the WebSocket layer even though REST accepted it.
**Where you actually see them:** neither propagates on its own. Both are
raised inside `_ws_authenticate`, called from `watch_states`, whose outer loop
catches every exception and logs the next message instead:

```text
state watch disconnected (%s); retrying in %.0fs
```

**Cause:** the state-change websocket dropped — a Home Assistant restart, a
network blip, or either auth failure above. Reconnects automatically with
exponential backoff up to 60 seconds, so a single occurrence during an HA
restart is normal.
**Fix:** if it repeats forever, check `home_assistant.token` and that nothing
between Maverick and Home Assistant blocks WebSocket upgrades.
**Config key:** [`displays[].schedule.on_change`](reference/configuration.md#displaysschedule).

---

## Rendering

`RenderError` (`src/maverick/render/dashboard.py:40`) and the browser-startup
`RuntimeError` are both caught by `Engine.render()`. A `RenderError`'s text
lands in `state.last_error` unchanged; any other exception (the Chromium
`RuntimeError` included) is prefixed with its type name —
`f"{type(exc).__name__}: {exc}"` — and the full traceback is also written to
the log via `log.exception`.

### Chromium will not start

```text
Could not start Chromium: {exc}
Run `playwright install chromium`, or point MAVERICK_CHROMIUM_PATH at an existing Chromium binary.
```

**Cause:** Playwright has no Chromium binary at the path it expected —
usually because `playwright install chromium` was never run, or because it
was run on a platform Playwright ships no build for.
**Fix:** run `playwright install chromium`. On **aarch64** (a Raspberry Pi
running Home Assistant OS) that will not help — Playwright ships no Linux ARM
build — install the distribution's `chromium` package instead and set
`MAVERICK_CHROMIUM_PATH=/usr/bin/chromium`. See
[Chromium on ARM](#chromium-on-arm) below.
**Env var:** `MAVERICK_CHROMIUM_PATH`.

### A browser context that will not close

```text
could not close the browser context %s: %s
```

**Cause (log, warning):** Chromium was asked to discard a cached browser
context — after an auth failure, or because the display it belongs to was
changed or removed (`BrowserPool.drop_context` and `drop_contexts_for` in
`src/maverick/render/browser.py`) — and refused, which in practice means the
browser has already gone. Closing a context is cleanup, so it is logged rather
than raised: letting it fail the removal would leave the transport running for
a display nothing renders any more.
**Fix:** nothing, unless it repeats. A context that could not be closed is
already out of the pool, and the next render builds a fresh one. If every
render then fails with a Chromium error, the browser really has died and
restarting Maverick relaunches it.
**Surfaces:** log only.

### Theme CSS file

```text
theme.css_file not found: {path}
```

**Cause:** [`theme.css_file`](reference/configuration.md#displaystheme) names
a path that does not exist. Deliberately fails the render rather than
silently rendering without it.
**Fix:** fix the path, or unset `theme.css_file`.

### Login page or blank frame

```text
[{display.id}] Home Assistant redirected to the login page. The access token is missing, expired or was issued for a different URL — home_assistant.url must match the origin the token was created on, scheme and port included.
```

```text
[{display.id}] Home Assistant showed the login form. The credential was rejected: re-link the account from the setup UI, or check home_assistant.token is a long-lived access token.
```

**Cause (both):** the frontend never accepted the seeded `hassTokens` bundle
— see [the URL rule](guides/home-assistant.md#the-url-rule). The first fires
on the URL Chromium actually landed on (`/auth/authorize` or
`/lovelace/login`); the second on a login element still present in the DOM
after `wait_for_selector` passed some other way.
**Fix:** make `home_assistant.url` (or `home_assistant.frontend_url`, if the
frontend is on a different origin than the API) byte-for-byte the origin the
token was issued on — scheme and port included — and confirm the token is a
long-lived access token, not a supervisor token. Set
`render.debug_artifacts: true` and re-run to see the actual screenshot at
`<data_dir>/debug/<id>/screenshot.png`.
**This is also what the [`blank_render`](design-guide.md#blank_render) lint
finding exists to catch** — see [Risks that hurt most](#a-stale-login-page-frame-on-a-battery-panel)
below.

### Selector never seen

```text
[{display.id}] never saw {selector!r} at {url}. If this is not a Home Assistant dashboard, set render.wait_for_selector to something on the page.
```

**Cause:** neither the default `home-assistant` element nor a configured
[`render.wait_for_selector`](reference/configuration.md#displaysrender)
appeared within `render.timeout`. Usually a dashboard path that 404s, a slow
first load exceeding the timeout, or (per the message) a non-Home-Assistant
page.
**Fix:** check `dashboard:` is a real path, raise `render.timeout`, or point
`wait_for_selector` at something that actually exists on the page.

### Crop selector matched nothing

```text
[{display.id}] crop_to_selector {render.crop_to_selector!r} matched nothing.
```

**Cause:**
[`render.crop_to_selector`](reference/configuration.md#displaysrender) is set
but no element on the page matches it — a typo, or a card that only renders
under conditions the page did not meet this time.
**Fix:** check the selector in the browser's own devtools against the same
dashboard URL, or unset `crop_to_selector` to capture the whole page.

---

## The lint gate

The linter (`src/maverick/eink/lint.py`) inspects the *quantised* frame — what
the panel will actually show — after every render. Its findings are not
raised as exceptions; they are collected on `frame.lint.issues` and printed by
the CLI, logged, shown on the setup UI card, returned by the HTTP API, and
written to `<data_dir>/debug/<id>/lint.json` when `render.debug_artifacts` is
on. Only `error`-severity findings block delivery, and only while
[`block_on_lint_error`](reference/configuration.md#top-level-keys) (default
`true`) is set — `maverick render --force` overrides it for one render.

A blocked render surfaces as:

```text
blocked by lint: {issues}
```

— `state.reason`/`state.last_error`, built by joining every error finding's
own message with `; `. Every finding code, its message template, its
threshold and its fix is documented in
**[the design guide, § 6 "The linter, finding by finding"](design-guide.md#6-the-linter-finding-by-finding)**:

| Code | Severity | Design guide |
|---|---|---|
| `empty_frame` | error | [§ `empty_frame`](design-guide.md#empty_frame) |
| `blank_render` | error | [§ `blank_render`](design-guide.md#blank_render) |
| `heavy_ink` | warning | [§ `heavy_ink`](design-guide.md#heavy_ink) |
| `spot_ink_overuse.<ink>` | warning | [§ `spot_ink_overuse`](design-guide.md#spot_ink_overuse) |
| `hairlines` | warning | [§ `hairlines`](design-guide.md#hairlines) |
| `dither_speckle` | warning | [§ `dither_speckle`](design-guide.md#dither_speckle) |
| `sub_threshold_pixel` | info | [§ `sub_threshold_pixel`](design-guide.md#sub_threshold_pixel) |
| `palette_underused` | info | [§ `palette_underused`](design-guide.md#palette_underused) |

`blank_render` is the one that matters most: see
[A stale login-page frame on a battery panel](#a-stale-login-page-frame-on-a-battery-panel).

---

## Delivery

Every transport reports failure the same way, by returning
`DeliveryResult.failure(...)` (`src/maverick/transports/base.py`) instead of
raising. The text becomes `state.last_error` and `outcome.reason` exactly as
written here.

### HTTP pull (`http_pull`)

```text
frame store unavailable (server not running)
```

**Cause:** a display uses the `http_pull` transport but Maverick was not
started through the HTTP server (`Application`/`create_app`) — the frame
store the transport writes to and the device fetches from does not exist.
Not reachable through `maverick serve`; only through scripted use of the
engine on its own.

```text
server.base_url is not set, so devices cannot be told where to fetch from. Set it to a URL reachable from the panel.
```

**Where:** `probe()` — surfaces via `maverick check` and the setup UI's probe,
not delivery itself (delivery still publishes the frame; devices just have no
URL to be told about).
**Fix:** set [`server.base_url`](reference/configuration.md#server) to an
address the panel itself can reach — not `localhost`, and not the add-on's
internal hostname unless the panel is on the same Docker network.

### File (`file`)

```text
could not write {directory}: {exc}
```

**Cause:** an `OSError` writing the frame — the directory does not exist and
could not be created, or the process lacks permission.
**Fix:** check `transport.path` is writable by the user Maverick runs as.
**Config key:** `transport.path` (default `./out`).

### Webhook (`webhook`)

```text
{method} {url} returned {response.status_code}: {response.text[:200]}
```

**Cause:** the endpoint responded with a 4xx/5xx.
**Fix:** check the URL, and read the response body Maverick captured for the
actual reason.

```text
{method} {url} failed: {exc}
```

**Cause:** the request itself failed — DNS, connection refused, TLS, or a
timeout past `transport.timeout` (default 30s).
**Fix:** confirm the URL is reachable from wherever Maverick runs.

### MQTT (`mqtt`)

```text
MQTT is not enabled. Set mqtt.enabled: true and configure the broker.
```

**Cause:** a display's `transport.type` is `mqtt` but
[`mqtt.enabled`](reference/configuration.md#mqtt) is `false`, so no publisher
exists.
**Fix:** set `mqtt.enabled: true` and fill in `mqtt.host`/`mqtt.port`. The
same underlying condition is reported as the shorter `MQTT is not enabled`
from `probe()`.

```text
MQTT publish to {base} failed: {exc}
```

**Cause:** the connection dropped between `MqttPublisher.start()` succeeding
and this publish, or the QoS-1 handshake did not complete within 15 seconds.
**Fix:** check the broker's own log; this is a broker-side or network problem,
not a config one.

```text
broker not connected
```

**Where:** `probe()`, when a publisher exists but `MqttPublisher.connected` is
false — see [MQTT and discovery](#mqtt-and-discovery) for why that happens.

### OpenDisplay (`opendisplay`)

```text
opendisplay mode 'ha' needs a Home Assistant connection; set home_assistant.url and home_assistant.token, or use mode: ble.
```

**Cause:** `transport.mode` is `ha` (or `auto` resolved to `ha`) but Maverick
has no working Home Assistant connection.
**Fix:** configure `home_assistant.url`/`token`, or set `transport.mode: ble`
to talk to the tag directly (needs a local Bluetooth adapter).

```text
cannot write to {media_dir} ({exc}). The add-on needs the 'media:rw' mapping, or set transport.media_dir to a shared path.
```

**Cause:** `transport.media_dir` (default `/media/maverick`) is not writable
— most often the Home Assistant add-on missing the `media:rw` mapping.
**Fix:** add the mapping, or point `transport.media_dir` at a directory both
Maverick and Home Assistant can reach.

```text
media_dir {media_dir} is not inside media_root {media_root}; Home Assistant can only read images from its media folder.
```

**Cause:** `transport.media_dir` was overridden to somewhere outside
`transport.media_root` (default `/media`) — Home Assistant's media source can
only serve files under its media root.
**Fix:** keep `media_dir` nested under `media_root`, or move `media_root` to
match.

```text
opendisplay.upload_image failed: {exc}. Check the OpenDisplay integration is set up and device_id is correct — it is the device registry id, not the entity id or the MAC.
```

**Cause:** the `opendisplay.upload_image` service call itself failed — most
often a wrong `transport.device_id`.
**Fix:** find the device registry id in Home Assistant's device page for the
tag (not its entity id, not its MAC) and set `transport.device_id`.

```text
py-opendisplay is not installed. Install it with `pip install 'maverick-eink-dashboard[opendisplay]'`, or use mode: ha to deliver through Home Assistant's Bluetooth instead.
```

**Cause:** `transport.mode: ble` (direct BLE) needs the optional
`py-opendisplay` dependency, which is not installed. The same condition is
reported as the shorter `py-opendisplay is not installed` from `probe()`, and
again — worded for the CLI — from `maverick scan`.
**Fix:** `pip install 'maverick-eink-dashboard[opendisplay]'`, or switch to
`mode: ha`.

```text
BLE upload to {mac or device_name} failed: {exc}
```

**Cause:** the direct-BLE transfer failed — out of range, wrong encryption
key, or the tag already connected to something else.
**Fix:** re-run `maverick scan` to confirm the tag is in range and its MAC;
check `transport.encryption_key` if the tag requires one.

```text
transport.device_id is not set
```

**Where:** `probe()`, `ha` mode, when `transport.device_id` was never set.

```text
tag {mac or '(unset)'} not found. In range: {listing}
```

**Where:** `probe()`, `ble` mode — the scan completed but did not see
`transport.mac`. The listing shows what *was* found, which is usually the
fastest way to spot a typo'd MAC.

---

## MQTT and discovery

Beyond the `mqtt` transport's own delivery failures above, MQTT has its own
connection and command-handling messages.

```text
Timed out connecting to the MQTT broker at {self._config.host}:{self._config.port}. Check mqtt.host, credentials, and that the broker is running.
```

**Where:** raised by `MqttPublisher.start()`, called from `Engine.start()`,
where it is caught and logged as the next message rather than stopping the
service:

```text
MQTT unavailable: %s
```

**Cause (both):** the broker did not complete the connection handshake within
15 seconds — wrong `mqtt.host`/`mqtt.port`, the broker not running, or (less
often) credentials so wrong the broker never replies at all.
**Fix:** confirm `mqtt.host` and `mqtt.port`; `core-mosquitto` (the default)
only resolves from inside Home Assistant's own Docker network, so running
standalone needs the broker's real address.
**Surfaces:** log only — the server keeps running with MQTT disabled for the
rest of that process's life; every display appears without its Home Assistant
device until Maverick is restarted with the problem fixed.

```text
MQTT connection refused: %s
```

**Cause:** the broker actively rejected the CONNECT packet — bad
`mqtt.username`/`mqtt.password`, or an ACL denying the client id.
**Fix:** check credentials and that `mqtt.client_id` (default `maverick`) is
allowed to connect. A duplicate `client_id` on the same broker also shows as
connect/disconnect churn rather than this message.
**Surfaces:** log only (`on_connect` callback).

### Commands Home Assistant sends back

`button.<name>_refresh`, `button.<name>_full_refresh` and
`switch.<name>_scheduled_renders` all publish to
`{base_topic}/display/{id}/command`, which `Application._subscribe_commands`
handles:

```text
unparseable MQTT command on %s
```

**Cause:** something published to a display's command topic with a payload
that is not valid text, or a topic that does not have exactly the expected
`.../display/<id>/command` shape.
**Fix:** only Maverick's own discovery entities should publish here; check
nothing else is publishing to `{base_topic}/display/+/command`.

```text
[%s] unknown command %r
```

**Cause:** a recognised topic, but a payload that is none of `refresh`,
`PRESS`, `press`, `full_refresh`, `schedule_on` or `schedule_off`.
**Fix:** as above — this is diagnostic, not something to configure around.

```text
command for unknown display %r
```

**Cause:** the topic names a display id no longer in the config — typically a
retained command message left over from a display you removed. Retained
discovery messages for a removed display should be retracted with
`MqttDiscovery.remove_display`, but a stray retained *command* on the old
topic will still trigger this once.
**Fix:** harmless; clear the retained message on that topic if it bothers
you (`mosquitto_pub -r -n -t '<topic>'`).
**Surfaces:** log only, all three.

---

## The scheduler

```text
on_change is configured for %d entities but Home Assistant is not connected; state triggers are inactive
```

**Cause:**
[`displays[].schedule.on_change`](reference/configuration.md#displaysschedule)
names entities, but `home_assistant.token` is empty (or `check()` failed at
startup), so there is no client to open the state-change websocket with.
**Fix:** set `home_assistant.token`, or drop `on_change` and rely on `every`
or `cron` alone.
**Surfaces:** log only, at startup.

There is no error path for `every`/`cron` themselves once the config has
loaded — a bad `cron` expression is rejected as a `ConfigError`-shaped
`ValueError` at build time (`invalid cron expression {schedule.cron!r}: {exc}`,
from `RenderScheduler._build_trigger`), which surfaces the same way as
[Loading the config](#loading-the-config) because it happens while
`RenderScheduler.start()` is still setting up.

---

## Changing a display while it runs

A display can be added, changed or removed without a restart:
`Application.add_display`, `update_display` and `remove_display`
(`src/maverick/app.py`) apply the change to the engine, the scheduler, MQTT
discovery and the display store in that order. Chromium is never restarted —
only the display's own browser contexts are dropped
(`BrowserPool.drop_contexts_for`), because every other panel is rendering
through the same browser.

Two of those steps can fail after the earlier ones have succeeded, and both say
what state that leaves you in.

```text
[%s] could not restart the previous transport after a failed update: %s. The display keeps its old config and builds a transport on its next render.
```

**Cause (log, error):** an update whose new transport would not start — a
`mqtt` transport with no broker, an `opendisplay` one without `py-opendisplay`
installed — is undone, and putting the *previous* transport back failed too.
That is the same failure twice: whatever stopped the new one (a broker that has
gone away, a missing dependency) generally stops the old one as well.
**Fix:** fix the underlying transport problem — see
[Delivery](#delivery) for the message the transport itself logged first — then
render the display once. `Engine.render` builds a transport for a display that
has none, so a successful render is also the repair.
**Surfaces:** log. The update's own error is what the caller is told; this line
is the extra detail.

```text
could not write the display store %s: %s. The change is live now, but it will be lost when Maverick restarts.
```

**Cause (log, error):** the display was added, changed or removed in the
running service, and then `<data_dir>/displays.yaml` could not be written — a
read-only `data_dir`, a full disk, or the wrong owner, exactly as for
[the display store](#the-display-store) at startup.
**Fix:** make `data_dir` writable by the user Maverick runs as, or point
`displays_file` somewhere that is, then make the change again to write it down.
The panel keeps rendering with the change in the meantime; it is only the file
that is behind.
**Surfaces:** log, and the failure is raised so the caller is told as well —
the change is deliberately *not* undone, because a panel that is rendering
correctly should keep rendering.

---

## Risks that hurt most

These are the four risks from
[the roadmap's risk table](roadmap.md#risks-worth-pricing-in),
restated as things to check rather than things to price.

### A stale login-page frame on a battery panel

An expired or mismatched token does not fail loudly — the Home Assistant
frontend silently redirects to its login screen, and a naive renderer
screenshots that instead of your dashboard. Pushed to a battery panel, that
frame then sits there until the next scheduled render, which on a quiet
schedule can be most of a day.

Two independent checks stop this from reaching a panel:

1. **`DashboardRenderer._verify_authenticated`**
   (`src/maverick/render/dashboard.py`) checks the URL Chromium actually
   landed on and the DOM for a login element, and raises
   [`RenderError`](#login-page-or-blank-frame) immediately — before any
   screenshot is even taken.
2. **The [`blank_render`](design-guide.md#blank_render) lint finding** is the
   backstop for everything the first check does not catch — a login page
   rendered under a selector that still matched, or any other reason the page
   came back nearly blank. It fires when a single ink covers 99.5%
   (`lint.blank_ratio`) of the frame, is an **error**, and — because
   [`block_on_lint_error`](reference/configuration.md#top-level-keys) defaults
   to `true` — blocks delivery outright.

Both are on by default; you would have to deliberately raise
`lint.blank_ratio`, set `block_on_lint_error: false`, or run
`maverick render --force` to let a blank frame through. If you ever do see a
stale login page on a panel, it means one of those was overridden, or the
frame predates both checks being in place.

### Chromium on ARM

Playwright ships no Chromium build for Linux aarch64, which is exactly the
architecture Home Assistant OS runs on a Raspberry Pi. `BrowserPool.start()`
(`src/maverick/render/browser.py`) fails with
[`Could not start Chromium`](#chromium-will-not-start) there every time,
regardless of whether `playwright install chromium` was run.

**Fix:** install the distribution's own Chromium package (`apt install
chromium` on Debian-based images) and set `MAVERICK_CHROMIUM_PATH` to its
binary — typically `/usr/bin/chromium` or `/usr/bin/chromium-browser`.
`BrowserPool` reads the environment variable itself if `executable_path` is
not passed explicitly, so no config change is needed beyond the environment.

### ESP32 memory ceiling

A full-colour or 16-grey frame at panel resolution can need several hundred
kilobytes to decode — a plain ESP32 has roughly 200 KB of usable heap. The
failure mode on the device is an allocation error at flash or fetch time that
says nothing about panel choice, so `generate_esphome_config`
(`src/maverick/esphome/generator.py`) computes the buffer size up front and
writes a comment block into the generated file whenever it exceeds 180 KB:

```text
# WARNING: this frame needs {buffer // 1024} KB of RAM to decode, which a plain
# ESP32 does not have (~200 KB usable heap).
```

— followed by the three options the generator itself recommends, in order:
add PSRAM (and it emits the `psram:` block for you), drive the panel from a
Raspberry Pi over the `mqtt` transport instead, or reduce the panel's colour
depth in Maverick. See
[the ESPHome/Waveshare recipe](recipes/esphome-waveshare.md) for the buffer
arithmetic and which catalogue panels need PSRAM.
**Config keys:** `displays[].esphome.buffer_size` (0 auto-sizes),
`displays[].esphome.board`, `displays[].color_scheme`.

### Colour panels looking muddy

Spectra 6 and ACeP colour panels have a small, muted gamut compared to sRGB.
Quantising against the *nominal* red/green/blue/yellow primaries — what a
browser thinks those colours are — looks visibly wrong on the ink, because the
panel's actual pigments do not reach those primaries.

**Fix:** measure your panel's real ink colours (a photo under consistent
light, sampled per patch) and set
[`image.palette_overrides`](reference/configuration.md#displaysimage) with
them, keyed by palette name (`black`, `white`, `red`, `yellow`, ...). Both the
quantiser and the injected stylesheet's colours read from the same overrides,
so the on-screen preview and the panel agree once they are set. See
[§ "The measured inks"](design-guide.md#the-measured-inks) in the design
guide for worked values and how they were measured.

---

## Reading the state

Everything Maverick remembers between renders lives under `data_dir`
(default `./data`), in two independent stores owned by `Engine`
(`src/maverick/engine.py`) — and, beside them, the file that says what the
displays are (`src/maverick/store.py`).

### `data_dir/state.json`

One `DisplayState` per display id, written after every render attempt
(`Engine._save_state`) and loaded back at startup (`Engine._load_state`):

| Field | Meaning |
|---|---|
| `sequence` | Frames successfully delivered; included in MQTT `meta` payloads. |
| `frames_since_full` | Counts toward `schedule.full_refresh_every`, across restarts. |
| `last_checksum` | Backs `schedule.skip_unchanged` — an identical checksum skips delivery. |
| `last_render_at` / `last_delivery_at` | ISO timestamps, shown on the setup UI card and the MQTT `sensor.<name>_last_render`. |
| `last_error` | The most recent failure message from anywhere in this catalogue. |
| `consecutive_failures` | Drives `status: error` on the MQTT state topic and `binary_sensor.<name>_problem`. |
| `last_pulled_at` | Set when a pull-transport device actually fetches its frame. |
| `render_count` / `skip_count` | Counters, for `sensor.<name>_frames` and your own judgement. |

If `state.json` cannot be parsed, it is discarded wholesale rather than
failing startup:

```text
ignoring unreadable state file %s: %s
```

— every display starts from a fresh `DisplayState`, which mainly means the
next render is never treated as "unchanged" and the full-refresh counter
resets to zero. Writing it back also degrades rather than crashes:

```text
could not persist state: %s
```

**Cause (both):** disk full, permissions, or (for the read case) a
half-written file from a hard kill — `_save_state` writes to `state.json.tmp`
and renames over the target, so a clean shutdown can never leave a torn file;
an unclean one (`kill -9`, power loss) still can.
**Fix:** check `data_dir` is on writable, non-full storage and owned by the
user Maverick runs as.

### `data_dir/frames/`

One `StoredFrame` per display — the payload bytes, a PNG preview, and its lint
summary and metrics — persisted by `FrameStore` (also `src/maverick/engine.py`)
as three files per display: `<id>.frame`, `<id>.preview.png`, `<id>.json`.
This is what a pull-transport device fetches from
`GET /api/displays/{id}/frame`, and what the setup UI's preview image and
`GET /api/displays/{id}/preview.png` serve — without it, a device restarting
Maverick soon after would get a 404 instead of its last known-good frame.

A fourth file, `<id>.screenshot.png`, is the pre-quantisation capture
downscaled to panel resolution, kept when `render.keep_screenshot` is set
(`src/maverick/config.py`) and served at `GET
/api/displays/{id}/screenshot.png`. Unlike the other three, `Engine.render`
writes it directly rather than through a transport's `deliver`, so it exists
for every transport once a display has rendered — not only `http_pull`, the
one transport that ever calls `FrameStore.put`
(`src/maverick/transports/pull.py`).

The same degrade-not-crash pattern applies:

```text
ignoring unreadable stored frame for %s: %s
```

```text
could not persist frame for %s: %s
```

```text
could not persist screenshot for %s: %s
```

```text
could not restore the screenshot for %s: %s
```

**Cause and fix:** identical to the `state.json` pair above — a corrupt or
unwritable frame or screenshot is dropped or skipped, not fatal, and the
display serves `404` (via `/frame` or `/screenshot.png`) until its next
successful render replaces it.

Removing a display deletes the same four files (`FrameStore.remove`), and a
file that will not go is logged the same way:

```text
could not delete the stored frame %s: %s
```

**Cause (log, warning):** one of `<id>.frame`, `<id>.preview.png`,
`<id>.json` or `<id>.screenshot.png` could not be unlinked — a read-only
`data_dir`, or the wrong owner. The display is gone from the running service
regardless.
**Fix:** delete the file by hand, or leave it: nothing reads it unless a new
display is created with the same id, which would then start by serving that old
frame until its first render replaces it.

### `data_dir/history/`

One JSON file per display, `<id>.json`, holding up to the last 50 render
outcomes — trigger, success, timings, lint summary, checksum, full-refresh
flag and delivery detail — appended by `Engine._notify` on every render and
served newest-first at `GET /api/displays/{id}/history`. Unlike
`DisplayState.last_error`, a later success does not erase these rows, which is
the point: it is where a render that failed, was blocked by lint, or skipped
an unchanged frame is still visible after the panel recovers.

The same degrade-not-crash pattern applies:

```text
ignoring unreadable history file %s: %s
```

```text
could not persist history for %s: %s
```

**Cause and fix:** identical to the `state.json` pair above — a corrupt or
unwritable history file is dropped or skipped, not fatal; the display simply
starts (or continues) with an empty or shorter history until its next render
appends to it.

Removing a display deletes its history file, and one that will not go is
logged the same way:

```text
could not delete history file %s: %s
```

**Cause (log, warning):** `<id>.json` under `data_dir/history/` could not be
unlinked — a read-only `data_dir`, or the wrong owner. The display is gone
from the running service regardless.
**Fix:** delete the file by hand, or leave it: nothing reads it unless a new
display is created with the same id, which would then start by showing that
old history until its own renders replace it.

### `data_dir/displays.yaml`

The displays themselves, as `DisplayStore` writes them (`src/maverick/store.py`):
a `version: 1` key and one entry per display, each holding only what differs
from a default display, so it reads like the `displays:` list it replaces.
`displays_file` moves it; empty means this path.

Unlike the two stores above it is not a cache — it is the configuration — so
deleting it does not reset anything, it removes every display. It also outranks
the config file: while it exists, a `displays:` list in `maverick.yaml` is
ignored with a warning ([above](#the-display-store)). To go back to configuring
displays in the config file, delete this file; the next start imports that list
again.

It is machine-owned. `${VAR}` in it is expanded on load, like everywhere else,
but a save rewrites the file whole with what the variable expanded to. A
substitution written here by hand therefore survives only until the next save —
keep secrets in the config file, which nothing rewrites.

### Resetting one display

Both stores are keyed by display id and independent of each other, so
resetting one display's state means deleting its own entries and nothing
else's, with Maverick stopped:

```sh
rm data/state.json            # or edit it and remove just that id's key
rm data/frames/<id>.*
```

Restarting then treats that display as never having rendered: no "unchanged"
skip, a full refresh on its next render regardless of
`full_refresh_every`, and (for a pull transport) a `404` from `/frame` until
that render completes. There is no `maverick` subcommand for this — it is a
plain file delete, because both stores are exactly the files above and
nothing else.

### `debug_artifacts`

Setting [`render.debug_artifacts: true`](reference/configuration.md#displaysrender)
on a display writes three extra files to `data_dir/debug/<id>/` on every
render (`Engine._write_debug`):

| File | Contents |
|---|---|
| `screenshot.png` | The pre-quantisation Chromium capture — what actually loaded, before dithering. This is the file to check first for a login page or a blank dashboard. |
| `frame.png` | The quantised preview, after the full pipeline. |
| `lint.json` | The same summary, metrics and issues as the API and the UI, as JSON. |

It is off by default because it doubles the disk written per render; turn it
on while diagnosing a [blank frame](#login-page-or-blank-frame) or a
[lint finding](#the-lint-gate) and back off once fixed.
