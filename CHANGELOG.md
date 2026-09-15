# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project has not
yet made a tagged release, so everything so far sits under Unreleased.

## [Unreleased]

### Added

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
- Documentation set: README, worked example config, the generated
  `docs/reference/` pages, the Home Assistant guide, device recipes for every
  transport, the e-ink design guide, the troubleshooting catalogue, the
  architecture evaluation and a self-maintaining contributor workflow with CI
  (`docs/`, `CONTRIBUTING.md`, `CLAUDE.md`, `.github/workflows/ci.yml`).
