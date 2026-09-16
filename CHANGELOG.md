# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
versions with [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **The header links the documentation.** It offered `api/docs` and nothing
  else — the OpenAPI page, which is the right answer for someone writing a
  client and no answer at all for someone asking how to build a dashboard for
  ink. The design guide, the docs index and the troubleshooting catalogue are
  named there now, and `tests/test_setup_ui.py` resolves each URL against the
  working tree so a renamed page cannot leave the UI pointing at a 404 —
  `scripts/check_links.py` checks relative Markdown links and never sees these.
- **A full-size view for the frame.** A card is one column of a responsive
  grid, so an 800×480 frame is shown at well under half size and a 1872×1404
  one at a fifth: enough to answer "did it render", useless for "is it
  legible", which is what the page is for. **Full size** beside Source/Frame —
  or clicking the thumbnail — opens the frame at the panel's own pixel size
  with `image-rendering: pixelated`, because a browser interpolating a
  dithered two-ink frame invents greys the panel cannot print. *Fit to window*
  is there for a frame larger than the screen, and *Open PNG* for a closer
  look still.

### Fixed

- **The Add display dialog had two scrollbars.** A `<dialog>` is
  `overflow:auto` in the UA stylesheet, so with a `max-height` it scrolls on
  its own, and the form inside took `max-height:inherit` — the dialog's
  *border-box* height — leaving it 2px taller than the content box holding it.
  Both scrolled, side by side. The dialog is now a flex column that does not
  scroll, and the form is sized to the space that exists.

## [0.4.0] - 2026-09-16

### Added

- **A starter dashboard, generated for the panel.** Maverick renders a
  dashboard you already have — and the one you already have was built for a
  phone, which is the commonest reason a first render looks wrong. There was
  no way out of that but to read a 1,100-line design guide the setup UI never
  linked to. `maverick dashboard <id>`, `GET
  /api/displays/{id}/dashboard.yaml` and a **Dashboard starter** disclosure on
  each card now emit a complete Lovelace configuration sized for that panel:
  the column count and the body-line budget come from its own pixels and dpi
  through the new `maverick.eink.layout` (which `scripts/design_tables.py`
  now imports too, so the design guide's column table and the generated
  layout cannot disagree), and the cards are only the ones the design guide
  finds bimodal. Entity ids come from Home Assistant through the new
  `HomeAssistantClient.list_states`; with no connection the same layout comes
  back with placeholders and a note in the file saying so. Every choice is a
  comment in the output, including the cards it refused to use and why.

### Changed

- **A display no longer has to name a transport.** `PanelProfile.default_transport`
  has described how each catalogued panel is actually reached since the
  catalogue existed, and `docs/reference/panels.md` documented it as "the
  transport a display uses if it sets none of its own" — but `TransportConfig.type`
  defaulted to the literal `"http_pull"`, so a display was never *not* naming
  one and a BLE shelf label published frames to an HTTP endpoint nothing would
  fetch. `type` is now optional and resolves through the new
  `DisplayConfig.transport_type`. Configs that name a transport are unaffected.
- **The Add display form asks four questions instead of seventeen.** Name,
  panel, dashboard and one Refresh picker; the id is derived from the name,
  the transport from the panel, and everything else — cron, quiet hours,
  `on_change`, the geometry overrides — is under Advanced. The `every`/`cron`
  pair in particular asked the user to know a crontab *and* to know which of
  two mutually exclusive fields wins; cron is now described as what it is, the
  override for a schedule an interval cannot express.
- **The setup UI points at the design guidance.** The Dashboard field says
  where to get a layout built for ink, each card's Dashboard starter links the
  design guide, and `docs/guides/home-assistant.md` has a "Building a
  dashboard for e-ink" section with the three rules that decide everything
  else.

### Fixed

- **The Add display dialog's panel and transport pickers were empty** once a
  display's Edit drawer had been opened. Both are built from the same cached
  form data, and `ensureAddDialogData` guarded on *that* rather than on
  whether this dialog had been filled in, so opening an editor first satisfied
  the guard and the dialog was never populated. Nothing failed and nothing was
  logged; the pickers were simply empty for the rest of the page's life.

## [0.3.0] - 2026-09-16

### Added

- **A browser regression suite.** `tests/conftest.py` stubs the renderer and
  the transports everywhere else, so nothing in `pytest -q` had ever opened a
  Chromium, and two findings `docs/architecture.md` calls out as needing one
  to fail in — seeding the Home Assistant auth bundle before the dashboard's
  own first script runs, and adopting the injected stylesheet into every
  shadow root, including one attached late — had no regression test. New
  `tests/browser/`, gated by a `browser` pytest marker
  (`pyproject.toml`) that `pytest -q` excludes by default so the existing
  suite stays exactly as fast, drives `DashboardRenderer` with a real
  `BrowserPool` against static fixture pages
  (`tests/browser/fixtures/dashboard.html`, `login.html`, served from a local
  `http.server` thread — no Home Assistant involved) to cover both findings,
  plus that a login page is rejected and its browser context dropped, and
  that the returned image is the viewport size times `supersample`. A third
  test drives the Phase 2 setup UI itself: a real FastAPI app under uvicorn,
  a display added through the Add display form, and its status pill followed
  from "rendering" to "ok". `tests/browser/conftest.py` skips the whole
  directory, rather than failing it, when no Chromium is reachable — the same
  rule `BrowserPool` uses. CI gains a `browser` job that installs Chromium and
  runs it for real. (#56)
- **A standalone Docker image, and a local dev loop against a real Home
  Assistant.** The root [`Dockerfile`](Dockerfile) is modelled on
  `app/Dockerfile` — Debian bookworm, a venv, Debian's `chromium` package, the
  same fonts and `HEALTHCHECK` — but installs the package from the build
  context rather than a pinned git ref, reads no `/data/options.json` and
  assumes no Supervisor; it runs
  `maverick -c /config/maverick.yaml serve --host 0.0.0.0 --port 5000` with
  `/config`, `/media` and `/share` as volumes. `docker-compose.dev.yml` stands
  it up next to a real Home Assistant (`dev/homeassistant/`, seeded with
  `demo:` entities and a dashboard) and Mosquitto (`dev/mosquitto.conf`), with
  the source tree bind-mounted so a code change needs only
  `docker compose restart maverick`; `scripts/dev.sh` wraps the loop
  (`up`, `down`, `logs`, `render <id>`, `check`). CI gains an `image` job that
  builds the root image on pull requests (amd64) and runs the same smoke
  commands the `app` job runs. See "Docker" in README.md and "Running against
  a real Home Assistant" in CONTRIBUTING.md. (#55)
- **Several dashboards per display, with rotation.** A display rendered the one
  dashboard named in its config, and `DisplayConfig` is `extra="forbid"`, so
  the `pages:` syntax `docs/roadmap.md` proposed was rejected at load. It is
  now real: a `PageConfig` (`src/maverick/config.py`) with a `dashboard`, an
  optional `name` — derived from the last segment of the path when empty — and
  an optional `dwell` parsed like `schedule.every`, plus `displays[].pages` and
  `displays[].rotate`. `dashboard` stays the single-page shorthand, and setting
  both it and `pages` is a load error naming the display; a display with no
  pages has exactly one, so everything downstream counts pages rather than
  asking which form it was written in. `DisplayState` gains `page_index` and
  `page_shown_at`, both persisted, so a restart does not undo what an
  automation selected, and `Engine.render` resolves the current page's
  dashboard at render time rather than at load. With `rotate: true`, a
  *scheduled* tick advances to the next page once the current page's `dwell`
  has elapsed (`RenderScheduler._rotate`), wrapping at the end and leaving
  renders someone asked for on the page that is up. Three ways to change it,
  all rendering under a new `page` trigger: `POST /api/displays/{id}/page` with
  `{"index": n}`, `{"name": "..."}` or `{"step": 1}` — the page moves before
  the response and the render runs behind it unless `?wait=true`; a **Page**
  select per display over MQTT discovery, whose options are the page names,
  with `page:<name>`, `next_page` and `previous_page` routed by
  `Application.handle_command`; and a page picker on each card in the setup UI
  with a Pages section in the editor drawer for adding, removing and
  reordering. The display summary gains `page` (index, name, dashboard, count,
  names and `rotate`), and the MQTT state topic gains `page` and `page_index`.
  (#58)
- **Render history per display.** `DisplayState` (`src/maverick/engine.py`)
  keeps only the *last* error and a failure count, both cleared by the next
  success, so a panel that fails one render in ten had no trace of it
  afterwards. `Engine` now keeps a bounded history per display — the last 50
  outcomes, each with its trigger, success, timings, lint summary, checksum,
  full-refresh flag and delivery detail — appended by `Engine._notify` on
  every path `Engine.render` can take (success, failure, a lint block or an
  unchanged skip) and persisted write-then-rename to
  `<data_dir>/history/<id>.json`, loaded at startup and deleted by
  `Engine.unregister_display`. `GET /api/displays/{id}/history?limit=N`
  (behind the API token, newest first, default 20, maximum 50) serves it, and
  each card in the setup UI gains a History disclosure — a compact table of
  time, trigger, outcome and duration, with failed and blocked renders
  highlighted and their reason shown on expand — that loads when opened and
  refreshes with the poll while open. (#54)
- **The pre-quantisation screenshot next to the quantised frame.** Answering
  "did the dashboard render wrong, or did the pipeline do this" used to mean
  Samba or SSH: `render.debug_artifacts` wrote the raw capture to
  `<data_dir>/debug/<id>/`, and nothing served it. `RenderConfig` gains
  `keep_screenshot` (`src/maverick/config.py`, on by default), and
  `Engine.render` downscales the raw capture to the panel's resolution — with
  a new `fit_to_panel` (`src/maverick/eink/pipeline.py`), factored out of
  `process` so both share the same fit-rotate-resize maths — and stores it in
  `FrameStore` as a fourth file, `<id>.screenshot.png`, written and restored
  the same way as the other three. Unlike those three, it is written directly
  by `Engine.render` rather than by a transport's `deliver`, so it exists for
  every transport once a display has rendered, not only `http_pull`.
  `GET /api/displays/{id}/screenshot.png` serves it (404 before the first
  render, or always with the flag off), and `POST /api/displays/preview`
  returns it too, as `screenshot_png`, so the editor's dry-run preview can
  show source and result together. The setup UI's cards and the editor's
  preview pane both gain a Source/Frame toggle over the image, defaulting to
  Frame. (#53)
- **A Dashboard picker, fed from Home Assistant.** `HomeAssistantClient` gains
  `list_dashboards()` (`src/maverick/ha/client.py`), built on a new private
  `_ws_call`, factored out of `watch_states`, for a one-shot WebSocket command
  request/response. It lists every Lovelace dashboard and its views — always
  including the default dashboard at `url_path: "lovelace"` — via the
  `lovelace/dashboards/list` and `lovelace/config` commands, so a dashboard
  whose configuration cannot be read (a YAML-mode dashboard) still appears,
  with an empty `views` list. `GET /api/ha/dashboards` (behind the API token)
  serves that list and answers `503` when there is no working Home Assistant
  connection. The Add display form and the per-display editor's Dashboard
  field now offer a `<datalist>` built from it, labelled with each dashboard
  and view's title; free text — an absolute URL or a `file://` page — is
  still accepted, and the picker fails quietly to a plain text field when the
  endpoint is unavailable. (#52)
- **A display can now be added, changed or removed while the service runs.**
  Every per-display resource was built in a start-up loop — the engine's
  transport, lock and state entry (`Engine.start`), the scheduler's job and the
  `on_change` subscription (`RenderScheduler.start`), the MQTT device
  (`MqttDiscovery.announce`) — so the only way to apply a change was a restart,
  which relaunches Chromium and, with `schedule.render_on_start` on by default,
  redraws every panel at once. Each of those steps is now callable for one
  display: `Engine.register_display`, `update_display` and `unregister_display`
  (`src/maverick/engine.py`), `RenderScheduler.add_display`, `remove_display`
  and `refresh_state_watch` (`src/maverick/scheduling/scheduler.py`), composed
  in that order by `Application.add_display`, `update_display` and
  `remove_display` (`src/maverick/app.py`), which then announces or retracts the
  Home Assistant device and writes the display store. Chromium is never
  restarted: only the display's own browser contexts are dropped
  (`BrowserPool.drop_contexts_for`, `src/maverick/render/browser.py`). An update
  keeps the state entry and the stored frame, because `frames_since_full`
  describes the panel rather than the config and a pull device that wakes before
  the next render still needs something to fetch; a removal deletes both
  (`FrameStore.remove`). Nothing reached this over HTTP yet — the entries below
  are what did. (#47)
- **`Engine.render_candidate` renders a display config without keeping
  anything** (`src/maverick/engine.py`): it renders a config that need not be
  registered, runs the pipeline and the linter, reports the findings instead of
  gating on them, and delivers nothing. No state entry, no stored frame, no
  write to `state.json`, and the browser context it rendered through is dropped
  again on the way out. This is what the Preview button calls. (#47)
- **`Engine.is_rendering(display_id)`** says whether a display is inside the
  locked section of `Engine.render` right now, so the API and the setup UI can
  report a render in progress rather than inferring it from a timestamp. (#47)
- **Displays are now kept in a file Maverick owns**, `<data_dir>/displays.yaml`
  by default and `displays_file` wherever else you want it
  (`src/maverick/store.py`). Until now a display existed only in the config
  file, which is read once at start-up and never written back
  (`load_config` in `src/maverick/config.py`) — under the Home Assistant app
  that is `/config/maverick.yaml`, edited with a separate file-editor app and
  applied by restarting, so there was nowhere for a setup UI to put a display
  somebody created in it. `load_config` now resolves the two in the one place
  every command goes through: an existing store is the source of the displays
  and a `displays:` list in the config file is ignored with a warning naming
  both files; with no store, the config file's list is imported into it once
  and read from the store from then on. Nothing changes for a `Config` built in
  memory — the store belongs to loading a file, not to the model — and a store
  that cannot be written is logged and survived, because a panel on the wall
  cares about the render, not about where the display was written down.
  `maverick check` prints which of the two files the displays came from. (#46)
- **A display can now be created, replaced, deleted and previewed over HTTP,**
  and its schedule paused at runtime, closing the gap the previous two entries
  left: `Application.add_display`, `update_display`, `remove_display` and
  `Engine.render_candidate` existed but nothing reached them over the API
  (`src/maverick/server/api.py`). `GET /api/schema/display` gives a form
  everything it needs to draw itself — `DisplayConfig.model_json_schema()` plus
  each registered transport's `options_doc`. `POST /api/displays` creates a
  display (**201**, **409** on a duplicate id, **422** naming the field for a
  bad or misspelt one — `extra="forbid"` doing its job); `PUT
  /api/displays/{id}` replaces one (**400** if the body's id does not match the
  path); `DELETE /api/displays/{id}` stops it and deletes its stored frames.
  `POST /api/displays/{id}/schedule` pauses or resumes the schedule at runtime
  through the same path the MQTT `schedule_on`/`schedule_off` commands take,
  distinct from the config-level `schedule.enabled` field that `PUT` covers.
  `POST /api/displays/preview` runs `Engine.render_candidate` and returns the
  frame as base64 PNG with its lint report, saving nothing. `POST
  /api/displays/{id}/render` gained `?wait=false`: **202** immediately with the
  render running as a background task, rather than blocking for the whole
  render timeout — which under ingress runs inside an iframe with no progress
  shown — with `?wait=true` (the default) keeping today's synchronous response
  for `rest_command` users. The display summary gained `rendering`
  (`Engine.is_rendering`), `next_run_at` (the scheduler's own job, which
  nothing called before this) and `last_render_s`/`last_total_s`, two new
  `DisplayState` fields (`src/maverick/engine.py`) that persist the last
  render's durations and are now published in the MQTT state payload
  alongside `render_duration` too. (#48)
- **The setup UI keeps itself up to date, and its buttons no longer reload the
  page.** It had no polling and no push, so a scheduled render in the
  background was invisible until someone pressed reload, and *Refresh* waited
  on a synchronous `POST` before calling `location.reload()`
  (`src/maverick/server/ui.py`). The cards are now built by
  `src/maverick/server/static/app.js` from `GET /api/displays`: the first copy
  of that payload is embedded in the page as a
  `<script type="application/json">` block so the first paint has content and
  waits for no request, and the script re-polls it every five seconds — paused
  while the tab is hidden — applying it to the cards in place rather than
  replacing them. Each card now shows what P1.3 began reporting and nothing
  displayed: the next scheduled render as a relative time with the absolute one
  in its `title`, how long the last render took, and a status pill that says
  `rendering`, `paused` or `disabled` as well as the lint summary. *Refresh*
  and *Full refresh* call `?wait=false` and follow the summary's `rendering`
  flag, so a slow dashboard no longer holds the click open for the whole render
  timeout; a *Pause*/*Resume* control drives
  `POST /api/displays/{id}/schedule`, and a disabled display offers *Enable*.
  The preview image is re-fetched only when the frame's checksum changes. (#49)
- **`GET /api/displays` now carries each display's own configuration** under
  `config`, in the shortest form that loads back as it (`dump_display`,
  `src/maverick/store.py`) — the same form the display store writes. Every
  other key in the summary is a resolved value or live state, so nothing over
  HTTP described a display the way `PUT` wants it back, and a client could only
  flip `enabled` by reconstructing the rest and losing whatever it did not know
  about. The setup UI's *Enable* action is the first caller. (#49)
- **"MQTT off" in the header now says what turning it on would give, and how.**
  It was a fact with nothing to act on, while "Home Assistant not connected"
  got a whole card. The chip opens a short explanation — the button, the
  switch, the image entity and the sensors that a display only becomes a Home
  Assistant device with (`src/maverick/ha/discovery.py`) — and the two ways to
  get a broker: the Mosquitto
  broker app, which the app picks up through the Supervisor, or the `mqtt_host`
  option (`app/run.sh`); and `mqtt.enabled: true` standalone
  (`src/maverick/config.py`). It is a `<details>` element, so it opens with no
  script. (#49)
- **An "Add display" dialog** in the setup UI, built entirely from the API a
  form needs: `GET /api/panels` fills a Panel select grouped by vendor, with
  the profile's notes shown once one is chosen; `GET /api/transports` fills
  the Transport select, and each transport's `options_doc`
  (`src/maverick/transports/base.py`) draws its own option fields underneath,
  with a note that `mqtt` needs MQTT enabled globally; `GET /api/schema/display`
  supplies every field's help text, so the copy in the dialog and the
  `Field(description=...)` in `src/maverick/config.py` cannot drift apart. The
  id derives from the name as it is typed — lower-case, `-` for spaces,
  matching the pattern `DisplayConfig._slug` enforces — and stays editable. An
  Advanced disclosure holds the geometry overrides (width, height, colour
  scheme, dpi, rotation, frame format), each placeholder showing the selected
  panel's own value, which `GET /api/panels` now also reports as `rotation`
  and `frame_format` (`PanelProfile.native_rotation` and `.default_format`).
  Submitting posts to `POST /api/displays`; a 422 is mapped onto the field
  named in pydantic's `loc`, a 409 marks the id field, and a network failure
  is shown in the dialog rather than as an alert. The empty-state card, which
  used to tell the user to edit the config file and restart, now offers the
  same dialog. (#50)
- **Every field of a display is editable in the setup UI.** An *Edit* action on
  each card opens a drawer — a side panel, full-screen at phone width — with a
  section for the display itself and one for each nested model of
  `DisplayConfig`: Schedule, Theme, Image, Render, Lint, Transport, Pack,
  ESPHome. Nothing in `src/maverick/server/static/app.js` names a field. The
  controls are generated from `GET /api/schema/display`, and the schema decides
  each one: a boolean becomes a switch, a number with `ge`/`le` a number input
  carrying those bounds, an enum a select, `list[str]` a comma-separated box,
  `image.palette_overrides` a table of ink names and RGB values with a swatch
  each, and `theme.extra_css` a textarea. Every field's own
  `Field(description=...)` in `src/maverick/config.py` is the help under it, so
  a field added to the model turns up in the drawer with nothing here to
  change. A field something else fills in when it is left empty shows what that
  would be as its placeholder — the panel profile's value for width, height,
  colour scheme, dpi, rotation and frame format, taken from the display
  summary's resolved fields, and for `name` the one `DisplayConfig._defaults`
  derives from the id. *Preview* posts the drawer's current state to
  `POST /api/displays/preview` and shows the frame that configuration would
  produce beside what the panel is showing now, side by side or as a
  cross-fading overlay, with the lint findings and their hints under it and the
  fact that it saved nothing said next to the button; the button counts the
  seconds while the render runs, and the rest of the drawer stays usable
  meanwhile. *Save* is a `PUT` that closes on success, with a **422** mapped
  onto the field named in pydantic's `loc` — a model validator such as
  `ImageConfig._levels` reports against the section instead — and the section
  holding the first error opened. *Delete* asks first, naming the display, and
  a drawer with unsaved changes asks before closing, Escape included; both
  questions are asked in the drawer, since a browser dialog inside Home
  Assistant's ingress iframe is not guaranteed to appear. The Transport section
  is the one the schema cannot describe, `TransportConfig` being the single
  `extra="allow"` model (`src/maverick/config.py`): it draws the selected
  transport's `options_doc` (`src/maverick/transports/base.py`) and keeps every
  key the stored configuration already has under `transport` on screen
  whichever transport is selected, because the file is the only record of
  those. Every other section sends known keys only, since one stray key fails
  the whole `PUT`. (#51)

### Changed

- **The Home Assistant app's options are read by the service, not by
  `run.sh`.** The options reached Maverick through bashio until now: `run.sh`
  read each one, exported it, and the starter `maverick.yaml` substituted it
  back. Three of the seven 0.2.x fixes were that chain rather than Maverick —
  `bashio::config key ''` handing back the *string* `"null"` for an unset
  option (0.2.4), `${VAR:-default}` not standing in for a variable exported
  empty (0.2.1), and `maverick serve -c file` being an argparse error (0.2.2)
  — and none of them could be reproduced outside a Supervisor. A new
  `src/maverick/ha/options.py` reads `/data/options.json` itself and sets the
  same environment variable names, so the starter config and every file a user
  has already edited are unchanged: a missing key, a JSON `null` and an empty
  string are all "unset", and the two derived values keep their old rules —
  `base_url` from the host's first IPv4 address (`GET /network/info`, prefix
  length stripped) and MQTT from the explicit `mqtt_host` or else the
  Mosquitto broker app (`GET /services/mqtt`), both through `api_get` in
  `src/maverick/ha/supervisor.py`, both degrading to the documented fallback
  when the Supervisor will not answer. The module's docstring records which
  permission grants each call, `hassio_api` being only one of the two.
  `cli._load` applies them whenever `SUPERVISOR_TOKEN` is set and the file
  exists (`src/maverick/cli.py`), which also retires the
  `/data/options.json` entry in `DEFAULT_CONFIG_PATHS` and the "The add-on
  should have generated one" error that entry existed to raise. `app/run.sh`
  is down to copying the starter config and starting the service, and CI now
  runs the built image against an `options.json` with nothing in the
  environment (`.github/workflows/ci.yml`) — the local reproduction the 0.2.x
  cycle did without. (#57)
- **The setup UI's stylesheet and script are files now, not strings in Python.**
  `_CSS` and `_JS` lived in `src/maverick/server/ui.py`, which also rendered
  every card server-side; they are now `src/maverick/server/static/app.css` and
  `app.js`, served by Starlette's `StaticFiles` mounted at `/static`
  (`src/maverick/server/api.py`) and listed in
  `[tool.setuptools.package-data]` (`pyproject.toml`) so they ship in the
  wheel. `render_ui` renders the shell — the header, the *Link with Home
  Assistant* card when no credential works, and an empty `<main>` — and the
  link card stays server-rendered because it has to work with no token and no
  script. The constraint that made them strings in the first place is
  unchanged: no build step, no framework, nothing fetched from a CDN, because
  this runs on a LAN that may have no internet. `/static` is not behind
  `server.api_token`, since the page that asks for the token is served exactly
  when the browser has none to offer. (#49)
- **The card grid no longer scrolls sideways on a narrow phone.**
  `minmax(340px, 1fr)` inside 24px of page padding is wider than a 360px
  screen, so the whole page moved horizontally; the columns are
  `minmax(min(340px, 100%), 1fr)` now, with a 16px gutter below 480px. The
  light and dark tokens are unchanged. Buttons carry `aria-busy` while a
  request is in flight, keyboard focus is visible again, and the one animation
  — the pulsing `rendering` pill — is dropped under
  `prefers-reduced-motion: reduce`. (#49)
- **A set `server.api_token` now switches each display's MQTT image entity to
  the frame bytes.** Discovery published an `image_topic` only when
  `server.base_url` was empty and a `url_topic` pointing at
  `/api/displays/{id}/preview.png` otherwise (`src/maverick/ha/discovery.py`).
  With that PNG behind the token (below), the URL would answer 401 to an image
  entity, which fetches with no credentials and has nowhere to put a token, so
  a token now picks the same branch an empty `base_url` does. The cost is the
  one the bytes mode always had: a retained PNG per panel on the broker. (#45)

### Fixed

- **The setup UI at `/` and `GET /api/displays/{id}/preview.png` ignored
  `server.api_token` entirely.** On the published port — 5000, which the app
  publishes for panels that pull frames (`app/config.yaml`) — anyone on the
  LAN could open the page and read every panel off it, however the token was
  set.
  Both now go through the same check as the render and frame routes
  (`src/maverick/server/api.py`). Requests arriving over the Home Assistant
  app's ingress are exempt, recognised by the peer address of the Supervisor's
  ingress proxy and only while running as an app
  (`src/maverick/ha/supervisor.py`): Home Assistant has already authenticated
  them and sends no token of ours. No header is trusted for this, because the
  published port takes requests from the whole LAN. (#45)
- **The setup UI's buttons failed silently when a token was set.** The page
  read the token only from its own query string, so unless the user knew to
  open `/?token=...` — which nothing on the page or in the documentation
  said — every fetch came back 401 and the button simply reported an error
  (`src/maverick/server/ui.py`). The page now asks for the token, keeps it in
  `sessionStorage` for the tab, sends it as `Authorization: Bearer` on every
  fetch, and appends `?token=` to the preview images and the ESPHome link,
  which cannot send a header. `/?token=...` still works and fills the same
  store. Through the app's ingress there was no query string to put a token in
  at all, so with a token set those buttons could not be made to work; ingress
  requests are now exempt. (#45)
- **`GET /api/displays/{id}/esphome.yaml` leaked `server.api_token` to anyone
  who could reach the server.** The generated ESPHome configuration embeds the
  token verbatim as an `Authorization: Bearer` header
  (`src/maverick/esphome/generator.py`), but the route serving it was the one
  `/api/displays/...` route without the `_require_token` dependency the
  render, list and frame routes carry (`src/maverick/server/api.py`). On the
  published port, an unauthenticated `GET` therefore returned the secret that
  gates every other endpoint — present since the route was first added. The
  route now requires the token like its siblings; the setup UI's "ESPHome
  config" link appends `?token=` itself when the page was opened with one
  (`src/maverick/server/ui.py`), since a plain anchor cannot send an
  `Authorization` header. (#44)

## [0.2.7] - 2026-09-15

### Changed

- **Re-release of 0.2.6 under a usable tag. No code changes.** The `v0.2.6`
  tag was published against the commit before the release, so it carried the
  0.2.5 tree — `version = "0.2.5"`, and without the `hassio_api` grant 0.2.6
  exists to add. Anything installing from that tag got 0.2.5 under a 0.2.6
  label, the mismatch `tests/test_app.py::test_pinned_ref_accepts_the_starter_config`
  guards against. A GitHub release cannot correct it by being republished:
  deleting a release leaves its tag behind, and `target_commitish` is only
  honoured when the tag has to be created, so the second attempt reused the
  same wrong tag. Rather than force-move it, 0.2.6 is abandoned and the same
  code ships as 0.2.7, whose tag name is free.

## [0.2.6] - 2026-09-15

### Fixed

- **The app could not fill in `base_url`, so the setup UI refused to start the
  link.** Left empty, `run.sh` derives it from the host's first IPv4 address —
  which `base_url`'s own option description promises — through
  `bashio::network.ipv4_address`, and that calls `GET /network/info`. The
  manifest never asked for Supervisor API access, and while the Supervisor's
  `api_bypass` list covers `/addons/self/...` (which is why writing the app's
  own options needs no permission) it does not cover `/network/...`
  (`supervisor/api/middleware/security.py`), so the call was refused with 403.
  Every app left on the default `base_url` therefore had none, and
  `_link_card` (`src/maverick/server/ui.py`) showed "Set `base_url` first"
  instead of *Link with Home Assistant*. Until 0.2.4 this was hidden behind the
  `"null"` bug, which made `base_url` a non-empty string and skipped the
  fallback entirely. `app/config.yaml` now declares `hassio_api: true`, which at
  the default role reaches the `/.+/info` endpoints and nothing else.
- **That message did not say where to set it.** It named `base_url` without
  saying that in the app it is an option on the Configuration tab, which is the
  one place a user reading it can act. It now says so, with an example.

## [0.2.5] - 2026-09-15

### Fixed

- **The setup UI did not work through Home Assistant's ingress.** 0.2.3 enabled
  ingress on the claim that "every path the UI emits is already host-relative",
  which is backwards: root-relative paths are exactly what a path-prefix proxy
  breaks. Ingress serves the page at `/api/hassio_ingress/<token>/` and proxies
  with that prefix stripped, telling the app nothing about it — the Supervisor
  sends no `X-Ingress-Path` (`supervisor/api/ingress.py`, `_init_header`) and
  builds its upstream URL as `http://<ip>:<port>/<path>` (`_create_url`). So
  every `/api/...` in the page resolved against Home Assistant's own origin:
  preview images 404ed, and *Link with Home Assistant* failed on a JSON parse
  error because its `fetch` never reached the app. The UI now emits every URL
  relative to the document, which resolves correctly under the prefix and on
  the published port alike; the authorize redirect also leaves the ingress
  iframe, since the callback lands on the app's own `base_url`.
- **The setup UI hid the link card exactly when it was needed.** It asked
  whether a Home Assistant client existed, but `Engine.start`
  (`src/maverick/engine.py`) builds one whenever a credential is *configured*
  and keeps it after a failed check, so a credential Home Assistant rejects was
  reported as "Home Assistant connected" — in the page header and in
  `/api/auth/status` — and the *Link with Home Assistant* card was suppressed,
  leaving no way to replace the broken credential from the UI. Both now report
  whether the last check actually succeeded (`Engine.ha_ok`).

## [0.2.4] - 2026-09-15

### Fixed

- A freshly installed Home Assistant app failed every render with
  `AuthError: Home Assistant rejected the token request (400): Invalid client
  id`, before anything had been linked. `run.sh` read its optional options as
  `bashio::config 'key' ''`, and bashio takes its own fallback as `${2:-null}`
  — `:-` substitutes on an empty argument too, so an unset option came back as
  the literal string `null` rather than an empty one. Nothing downstream
  caught it: `null` is not empty, so it passed every `${VAR:-default}` in the
  starter config and was stored as the value. `home_assistant.refresh_token`
  and `home_assistant.client_id` both holding `"null"` look exactly like a
  linked account to `build_token_source`
  (`src/maverick/ha/auth.py`), so Maverick refreshed a credential it never
  had and Home Assistant's IndieAuth validator rejected the client id
  (`homeassistant/components/auth/indieauth.py`, `verify_client_id`). The same
  string also reached `server.base_url`, where it is not an http(s) URL and so
  blocked the *Link with Home Assistant* button that would have fixed it, and
  `server.api_token`, where it gated the pull and trigger endpoints behind a
  token nobody had been told. Optional options are now read through a
  `config_or_empty` helper built on `bashio::config.has_value`, which treats
  both `null` and the empty string as unset.

## [0.2.3] - 2026-09-15

### Fixed

- The Home Assistant app's **Open Web UI** button linked to
  `http://[HOST]:5000/`, which Home Assistant resolves to the instance's
  external address (a Nabu Casa URL, for one) when opened remotely, with the
  raw container port appended — an address that connection can never reach.
  `app/config.yaml` now declares `ingress: true` and `ingress_port: 5000`, so
  the button opens the setup UI proxied through the Supervisor and embedded in
  Home Assistant instead. No application code changed: every path the UI
  emits (`src/maverick/server/ui.py`) is already host-relative, which is what
  lets it work unmodified behind the Supervisor's path-based ingress proxy.
  Port 5000 stays published for panels that pull frames directly.

## [0.2.2] - 2026-09-15

### Fixed

- The Home Assistant app exited immediately on every start, from the moment the
  app was added. `app/run.sh` ran `maverick serve -c "${CONFIG_FILE}"`, but
  `-c` is a global option that argparse only accepts before the subcommand
  (`build_parser`, `src/maverick/cli.py`; "Given before the subcommand" in
  `docs/reference/cli.md`), so the CLI exited 2 with a usage message and
  nothing else ever ran — not the config load, not the server. It now runs
  `maverick -c "${CONFIG_FILE}" serve`.
- The systemd unit in README.md carried the same broken argument order.

### Added

- `tests/test_app.py::test_run_sh_invokes_the_cli_the_way_the_cli_parses` feeds
  every `maverick` command line in `app/run.sh` to the real CLI parser. CI
  builds the image and exercises the package, but nothing executes the shell
  script that starts it — it needs bashio and a Supervisor to answer — so a
  typo there was invisible to every other check.

## [0.2.1] - 2026-09-15

### Changed

- `${VAR:-default}` in a configuration file now falls back for a variable that
  is set to the empty string as well as one that is unset, which is what `:-`
  means in a shell and what the reference page has always described it as
  (`expand_env`, `src/maverick/config.py`). Previously only an *unset* variable
  took the default, so `${MQTT_PORT:-1883}` yielded an empty string — and
  failed validation — when something upstream exported `MQTT_PORT=`. The Home
  Assistant app's `run.sh` exports a value for every substitution in the
  starter config, empty for the options a user has not filled in, so that was
  reachable rather than theoretical. `${VAR}` without a default is unchanged:
  unset still fails the load, set-and-empty still yields the empty string.

  If you relied on exporting an empty variable to blank a value whose
  configuration file writes a non-empty default beside it, that now yields the
  default. Write the value into the file, or use `${VAR}` without a default.

### Added

- `tests/test_app.py::test_pinned_ref_accepts_the_starter_config` loads the
  starter configuration through the config module of the commit
  `app/Dockerfile` pins, which is the package the app's image installs. That
  pairing is what broke 0.2.0's first start — the ref named a commit predating
  `home_assistant.refresh_token`, and models forbid unknown keys
  (`src/maverick/config.py:71-77`), so `maverick serve` exited on a validation
  error before the setup UI could be reached to link an account.
- `tests/test_app.py::test_starter_config_loads_with_no_credential_yet` covers
  the state a freshly installed app is in. The existing case passed a token, so
  an empty one — what `run.sh` warns about and starts anyway — was never
  exercised.
- CI loads the starter configuration inside the built image, checking the
  shipped template against the package `MAVERICK_REF` actually installed, and
  the test job checks out full history so the pinned commit is present
  (`.github/workflows/ci.yml`).

## [0.2.0] - 2026-09-15

### Added

- Linking a Home Assistant account from the setup UI: **Link with Home
  Assistant** runs the IndieAuth flow Home Assistant's companion apps use, so
  no long-lived token has to be copied by hand (`src/maverick/ha/auth.py`,
  `/api/auth/status`, `/api/auth/start`, `/api/auth/callback`). Running as an
  app, the credential is saved back to the app's own options through the
  Supervisor, which needs no extra permission (`src/maverick/ha/supervisor.py`).
- `home_assistant.refresh_token` and `home_assistant.client_id` config keys,
  holding a linked account. They take precedence over `home_assistant.token`.
- Imaging core: the fit/rotate/tone/sharpen/greyscale/quantise/lint/pack
  pipeline, palettes and dithering for monochrome and colour panels
  (`src/maverick/eink/`).
- Panel catalogue: `panels.yaml` and the `PanelProfile` loader describing every
  supported display (`src/maverick/devices/`).
- Config: the pydantic models for displays, transports, scheduling and Home
  Assistant, loaded from one YAML file (`src/maverick/config.py`).
- Renderer: a headless Chromium pool that loads a dashboard URL, injects the
  e-ink theme and screenshots it (`src/maverick/render/`).
- Engine: the orchestrator tying rendering, the persisted `FrameStore`, lint
  gating and delivery together (`src/maverick/engine.py`).
- Transports: BLE, MQTT, HTTP pull, webhook and file delivery behind a shared
  registry (`src/maverick/transports/`).
- Scheduler: intervals, cron, quiet hours, entity triggers and debouncing for
  when to render (`src/maverick/scheduling/`).
- Server: the FastAPI app and setup UI exposing the HTTP API
  (`src/maverick/server/`).
- CLI: `maverick serve|render|check|panels|transports|esphome|scan|init` for
  running and inspecting the service (`src/maverick/cli.py`).
- MQTT discovery: Home Assistant entity announcement and state/command topics
  for each display (`src/maverick/ha/`).
- ESPHome generator: device configuration generation for ESPHome-based panels
  (`src/maverick/esphome/`).
- Home Assistant app: a Debian image with Chromium and the package pinned to a
  commit, options for the connection, the pull base URL and MQTT (the
  Mosquitto broker app is used automatically), a starter `maverick.yaml`
  written on first start, and `repository.yaml` so this repository installs
  from the app store (`app/`, `repository.yaml`, `tests/test_app.py`).
- Documentation set: README, worked example config, the generated
  `docs/reference/` pages, the Home Assistant guide, device recipes for every
  transport, the e-ink design guide, the troubleshooting catalogue, the
  architecture evaluation and a self-maintaining contributor workflow with CI
  (`docs/`, `CONTRIBUTING.md`, `CLAUDE.md`, `.github/workflows/ci.yml`).

### Changed

- The Home Assistant app starts without a credential rather than exiting. The
  setup UI is where an account is linked, so it has to be reachable before one
  exists (`app/run.sh`).
- The access token is resolved per request instead of being baked into the REST
  client and the renderer's auth bundle, because a linked account's token
  expires every 30 minutes (`src/maverick/ha/auth.py`, `TokenSource`).
- The renderer seeds its auth bundle per page rather than per browser context,
  so a rotating token no longer changes the context cache key and strands a
  context on every refresh (`src/maverick/render/dashboard.py`).
