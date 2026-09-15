# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
versions with [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **A set `server.api_token` now switches each display's MQTT image entity to
  the frame bytes.** Discovery published an `image_topic` only when
  `server.base_url` was empty and a `url_topic` pointing at
  `/api/displays/{id}/preview.png` otherwise (`src/maverick/ha/discovery.py`).
  With that PNG behind the token (below), the URL would answer 401 to an image
  entity, which fetches with no credentials and has nowhere to put a token, so
  a token now picks the same branch an empty `base_url` does. The cost is the
  one the bytes mode always had: a retained PNG per panel on the broker.

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
  published port takes requests from the whole LAN.
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
  requests are now exempt.
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
  `Authorization` header.

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
