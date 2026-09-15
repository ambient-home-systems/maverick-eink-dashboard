# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
versions with [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
