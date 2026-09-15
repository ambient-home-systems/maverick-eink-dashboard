# Maverick — Implementation Plan

> **Interactive version with copy buttons:** https://claude.ai/artifact/Mb9jNsMaXeV1UkVhCdKwJw
>
> Reviewed 15 September 2026 against commit `29f95e1` (branch `main`, version 0.2.7). Line numbers in the prompts refer to that commit. The review this plan answers found the render service sound and the product layer absent; every finding from it is here.

## Where it stands

| Check | Result |
| --- | --- |
| `ruff check src tests scripts` | clean |
| `pytest -q` | 189 passed, 1 skipped, about 3 seconds |
| `python scripts/gen_docs.py --check` | reference pages up to date |
| `python scripts/check_links.py` | every relative link resolves |
| Open issues | none |
| Open pull requests | one, the release-ref bump for 0.2.7 |

## Verdict

The renderer, the image pipeline, the transports, the scheduler and the HTTP API are in good shape and well tested. The layer that would make Maverick usable is absent: configuration is read once at boot and never written, so there is no way to create, change or remove a display except editing YAML in another app and restarting the service, and the web page is a read-only status board. `docs/architecture.md` lists "the setup UI cannot change a display" as a known limitation, and `docs/roadmap.md` parks "UI setup instead of YAML" behind a custom integration that does not exist. That sequencing is why the feature has not happened.

One finding needs fixing before anything else. The ESPHome config route in `src/maverick/server/api.py` (line 255) carries no auth dependency, and `src/maverick/esphome/generator.py` (lines 101 to 105) embeds the bearer token when `server.api_token` is set. On the published port an unauthenticated GET returns the secret that gates every other endpoint.

The 0.2.1 to 0.2.7 run is seven patch releases in two days, every one of them packaging or authentication plumbing, each found in production because nothing local reproduces the Supervisor. Phase 4 exists so that grind does not repeat.

Everything the review found is in this plan. Nothing was dropped for being small.

## How to use this plan

- Each prompt is written for a fresh Claude Code session (web or CLI) with the repository checked out. Copy it whole; it carries its own context, acceptance criteria and verification steps, and it tells the session to read this file's entry for the task.
- Pick the model with `/model` and the effort with `/effort` before pasting. The recommendation on each card is the setting the task deserves, not a ceiling: a session that struggles can be re-run one step up. `xhigh` is Claude Code's default and the right setting for most of this work; `medium` is for the two small, fully specified fixes.
- Run the prompts in the order shown inside a phase. Across phases, Phase 0 comes first; Phase 4 is independent of Phases 1 to 3 and can run alongside them; Phase 5 comes last. The sequence table at the end says which prompts can be in flight at the same time.
- One prompt, one pull request. Merge each before starting the next in its chain, because later prompts assume the earlier code is on `main`.

### Model and effort

The effort levels on Opus 5, Sonnet 5 and Fable 5.1 are `low`, `medium`, `high`, `xhigh` and `max`.

| Model | Effort | Use it for |
| --- | --- | --- |
| Opus 5 (`claude-opus-5`) | `xhigh` | Work that spans the engine, the scheduler, the config model and a lifecycle, or that has subtle ordering: ingress, MQTT, bashio. The default for anything multi-file. |
| Sonnet 5 (`claude-sonnet-5`) | `high` | Well-specified changes in one area with an existing test pattern to copy: forms, endpoints, docs, the Docker image. |
| Sonnet 5 (`claude-sonnet-5`) | `medium` | Small fixes with an exact spec and a test to write. |
| Fable 5.1 (`claude-fable-5-1`) | `xhigh` | Optional, if you have access, for the two prompts where a mistake costs a release cycle: P1.2 (runtime lifecycle) and P4.1 (the app entrypoint). |

## The prompts at a glance

| # | Task | Model | Effort | Size | After |
| --- | --- | --- | --- | --- | --- |
| P0.1 | Close the API token leak in the ESPHome route | Sonnet 5 | `medium` | Small | — |
| P0.2 | Decide and implement what protects the UI on the published port | Opus 5 | `xhigh` | Medium | P0.1 |
| P1.1 | A displays store the service owns | Opus 5 | `xhigh` | Medium | — |
| P1.2 | Add, update and remove a display at runtime | Opus 5 | `xhigh` | Large | P1.1 |
| P1.3 | REST endpoints for displays, preview and control | Sonnet 5 | `high` | Medium | P1.2 |
| P2.1 | UI shell: static assets, live status, in-place actions, layout fixes | Opus 5 | `xhigh` | Large | P1.3 |
| P2.2 | The Add display form | Sonnet 5 | `high` | Medium | P2.1 |
| P2.3 | Per-display editor with a dry-run preview | Opus 5 | `xhigh` | Large | P2.2 |
| P2.4 | Dashboard picker from Home Assistant | Sonnet 5 | `high` | Medium | P2.2 |
| P2.5 | Source screenshot next to the quantised frame | Sonnet 5 | `high` | Medium | P2.1 |
| P2.6 | Render history per display | Sonnet 5 | `high` | Medium | P2.1 |
| P3.1 | Pages: several dashboards per display, with rotation | Opus 5 | `xhigh` | Large | P2.3 |
| P4.1 | Read the app's options in Python and shrink run.sh | Opus 5 | `xhigh` | Medium | — |
| P4.2 | Standalone image and a local dev loop against a real Home Assistant | Sonnet 5 | `high` | Medium | — |
| P4.3 | Browser regression tests for the renderer and the UI | Sonnet 5 | `high` | Medium | P2.2 |
| P5.1 | Reconcile the docs and re-sequence the roadmap | Sonnet 5 | `high` | Medium | P2.6, P3.1, P4.3 |

## Phase 0 — Stop the bleeding

Two authentication findings on the published port. The first is a secret leak and is a one-session fix. The second is a design decision the code has been avoiding: what, if anything, protects the setup UI and the previews on port 5000 when a token is set.

### P0.1 · Close the API token leak in the ESPHome route

**Model:** Sonnet 5 at `medium` · **Size:** Small · **After:** nothing

The generated ESPHome configuration embeds `server.api_token`, and the route that serves it is the one display route without the auth dependency.

Touches: `src/maverick/server/api.py`, `src/maverick/server/ui.py`, `README.md`, `docs/reference/http-api.md`, `tests/test_esphome_route_auth.py (new)`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P0.1 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. `GET /api/displays/{id}/esphome.yaml` (src/maverick/server/api.py, the route decorated at line 255) has no `dependencies=[auth]`, unlike the render, list and frame routes at lines 168 to 203. The generator it calls embeds the API token verbatim when one is set: src/maverick/esphome/generator.py lines 101 to 105 write `Authorization: "Bearer <server.api_token>"` into the YAML. On the published port an unauthenticated GET therefore returns the secret that gates every other endpoint. README.md's HTTP API table documents the route as "Token? no", and docs/reference/http-api.md describes it too.

Change.
1. Add `dependencies=[auth]` to the esphome.yaml route. `_require_token` (api.py lines 41 to 69) is a no-op when `server.api_token` is empty, so behaviour without a token is unchanged.
2. The setup UI links to the route as a plain anchor (src/maverick/server/ui.py, the "ESPHome config" link in each card's button row). With a token set that link now returns 401. Make it carry the token the page already reads from `?token=` (see `authHeaders()` in the page script): append `?token=<value>` to the href when one is present. Keep every URL document-relative (`api/...`, never `/api/...`); tests/test_setup_ui.py enforces that for ingress.
3. Update the README table row and docs/reference/http-api.md (hand-written prose, per the docstring of scripts/gen_docs.py) so both say the route is behind the token. Run `python scripts/gen_docs.py` and commit whatever it rewrites, because docs/reference/openapi.json is generated from the routes.
4. CHANGELOG.md: a Fixed entry under [Unreleased] saying what leaked, from where, and since when.

Tests. Add tests/test_esphome_route_auth.py using fastapi.testclient.TestClient and the fixture pattern in tests/test_setup_ui.py (monkeypatch Application.start and Application.stop to no-ops so no browser or scheduler starts). Cover: (a) with `server.api_token` set, an unauthenticated GET returns 401 and the body does not contain the token; (b) the same GET with `Authorization: Bearer <token>` returns 200 and the YAML contains the header; (c) the same with `?token=` works, since the UI link uses it; (d) with no token configured an unauthenticated GET returns 200. Keep tests/test_setup_ui.py green: its relative-path test resolves every `api/...` target the page emits, and a link that now carries a query string must still resolve.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

### P0.2 · Decide and implement what protects the UI on the published port

**Model:** Opus 5 at `xhigh` · **Size:** Medium · **After:** P0.1

The page at `/` and every preview PNG are unauthenticated by design so the Home Assistant image entity can fetch them. Behind ingress that is fine; on port 5000 anyone on the LAN sees every panel, and with a token set the page's own buttons silently fail unless the token is in the query string.

Touches: `src/maverick/server/api.py`, `src/maverick/server/ui.py`, `src/maverick/ha/discovery.py`, `src/maverick/ha/supervisor.py`, `app/DOCS.md`, `README.md`, `tests/test_setup_ui.py`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P0.2 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. Three routes are unauthenticated regardless of `server.api_token`: the setup UI at `/` (src/maverick/server/api.py line 450), `GET /api/displays/{id}/preview.png` (line 242) and, until P0.1, the ESPHome route. The preview is unauthenticated on purpose: when `server.base_url` is set, MQTT discovery publishes an image entity with `url_topic` pointing at that PNG (src/maverick/ha/discovery.py lines 135 to 156), and Home Assistant fetches it with no credentials; when base_url is empty it publishes the bytes over `image_topic` instead. Two consequences follow. On the published port anyone on the LAN can open the page and see what every panel shows. And when a token is set, the page's Refresh buttons only work if the user opened `/?token=...` (see `authHeaders()` in ui.py), which nothing on the page or in the docs says. Ingress (app/config.yaml, `ingress: true`) proxies requests to the app with Home Assistant's own login in front and no token of ours, so any gate we add must not break that path.

Decide, then implement. The design to implement is below; if you find a reason it cannot work, say so in the PR and implement the closest thing that does.
1. When `server.api_token` is empty, nothing changes.
2. When it is set, `/` and `preview.png` require the token too, through the same `_require_token` dependency (lines 41 to 69), with one exemption: requests arriving through Home Assistant ingress. Establish how to recognise those before writing code. The Home Assistant developer documentation for add-on ingress states the address the ingress proxy connects from on the add-on network, and the Supervisor's `supervisor/api/ingress.py` shows which headers it adds; verify both, cite what you find in the module docstring, and implement the exemption on the verified signal (the expected answer is the peer address of the Supervisor's ingress proxy, checked only when `running_under_supervisor()` from src/maverick/ha/supervisor.py is true). Never trust a header alone: anything the LAN can send is spoofable.
3. The image entity cannot fetch an authenticated URL, so when a token is set discovery must publish the frame bytes over `image_topic` (the branch that already exists for an empty base_url, and `publish_image` at line 221) rather than `url_topic`. Say in docs/reference/_mqtt intro or the MQTT reference prose that a token switches the entity to bytes over the broker.
4. The UI, when it gets a 401 from any of its own fetches, shows a small inline "API token" field, stores the value in `sessionStorage` (wrapped in try/except: private windows throw), and sends it as `Authorization: Bearer` on every later fetch. `?token=` in the URL keeps working and pre-fills the same store. Do not put the token in `localStorage`.
5. Documentation: the `api_token` row in app/DOCS.md, the README's HTTP API table (the "Token?" column for `/` and preview.png), docs/reference/http-api.md, and a short paragraph in docs/guides/home-assistant.md on what the token does and does not protect. CHANGELOG entry under [Unreleased].

Tests. Starlette's TestClient accepts a `client=(host, port)` argument, which is how to simulate the ingress peer address. Cover: with a token set, `/` and preview.png return 401 from an ordinary address and 200 with the token; from the ingress address under a monkeypatched `running_under_supervisor()` returning true they return 200 with no token; from the ingress address when not under the Supervisor they return 401 (the exemption must not exist outside the app); with no token everything is 200 as today. For discovery, extend the fake MQTT client pattern in tests/test_mqtt_will.py to assert the image entity payload uses `image_topic` when a token is set and `url_topic` when it is not. Keep tests/test_setup_ui.py's relative-path test passing.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

## Phase 1 — Runtime display configuration

Everything the review found missing traces to one absence: `cmd_serve` loads the YAML once and hands a frozen `Config` to the application; there are exactly two mutating routes, both of which render. This phase makes displays data the service owns, gives the engine and scheduler a per-display lifecycle, and exposes both over REST. The UI in Phase 2 is a thin client over this.

### P1.1 · A displays store the service owns

**Model:** Opus 5 at `xhigh` · **Size:** Medium · **After:** nothing

One source of truth for displays at runtime, persisted where the service can write it, with a one-time import from the YAML people have today.

Touches: `src/maverick/store.py (new)`, `src/maverick/config.py`, `src/maverick/cli.py`, `app/rootfs/usr/share/maverick/maverick.yaml`, `config.example.yaml`, `docs/reference/_configuration.intro.md`, `tests/test_display_store.py (new)`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P1.1 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. Displays live only in the config file. `load_config` (src/maverick/config.py line 1045) reads it, `_load` in src/maverick/cli.py (line 44) hands the result to `Application`, and nothing ever writes it back. For the app, the file is `/config/maverick.yaml`, which the user edits with a separate file-editor app and then restarts. For the UI to add or edit a display, the service needs a place it owns. docs/roadmap.md names the failure mode to avoid: two sources of truth for the same displays.

Design.
1. New module src/maverick/store.py with a `DisplayStore(path)` class: `exists()`, `load() -> list[DisplayConfig]`, `save(displays: list[DisplayConfig])`. The file is YAML, `{"version": 1, "displays": [...]}`, written with `model_dump(mode="json", exclude_defaults=True)` so it stays as short as a hand-written entry, and saved write-then-rename exactly as `FrameStore._persist` does (src/maverick/engine.py lines 199 to 216). Loading runs `expand_env` (config.py line 49) over the data before validation so `${VAR}` still works for anyone who hand-edits the file; document that a save writes resolved values back, so the store file is machine-owned and hand edits survive only until the next save. `TransportConfig` is `extra="allow"` (config.py lines 682 to 689); confirm its extra keys round-trip through dump and load, since that is where every transport option lives.
2. `Config` gains `displays_file: str = ""` with a description; empty means `<data_dir>/displays.yaml`. Under the app that resolves to /config/data/displays.yaml, next to the frame store, which the addon_configs folder exposes.
3. Precedence, applied in one place that every entry point uses (`load_config` is that place; `serve`, `render`, `check` and `esphome` all go through `_load`): if the store file exists, it is the source of displays, and a non-empty `displays:` list in the main config is ignored with one startup warning naming both files and telling the user they can delete the list. If the store file does not exist and the main config has displays, import them into the store, write the file, and log at info that N displays were imported and are managed in the setup UI from now on. If neither has displays the list is empty. Tests that build `Config.model_validate(...)` directly must be unaffected: the store step belongs to loading a file, not to the model.
4. `maverick check` prints which source it used. The empty-state text in the setup UI (src/maverick/server/ui.py line 195) will be replaced in P2.2; leave it, but make sure it no longer says a restart is required if you touch it.
5. The starter config the app writes (app/rootfs/usr/share/maverick/maverick.yaml) keeps its example display so a fresh install still has one, and its header comment explains that after the first start displays are managed in the Web UI and stored in data/displays.yaml. config.example.yaml gets the same note. docs/reference/_configuration.intro.md and the README's Configuration section explain the store and the precedence rule, with the source file named. tests/test_app.py loads the starter config through the real loader; keep it green and add the store to whatever it checks about files the app writes. CHANGELOG entry.

Tests. tests/test_display_store.py: round trip preserves only non-default keys and every transport extra; a `${VAR}` in a hand-edited store file is expanded on load and an unset one fails with the existing ConfigError message; duplicate ids in the store file fail with the same error `Config._unique_ids` gives; import-once (main config with displays, no store: the store is written, a second load reads the store); store-wins-with-warning (both present: caplog shows the warning naming both paths); write-then-rename leaves no .tmp behind. The new module has user-facing messages, so add it to `SOURCE_FILES` in tests/test_troubleshooting_coverage.py and document each message in docs/troubleshooting.md.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

### P1.2 · Add, update and remove a display at runtime

**Model:** Opus 5 at `xhigh` · **Size:** Large · **After:** P1.1

The engine and the scheduler build everything per display in startup loops. Make each step callable for one display, without restarting Chromium, and add a dry-run render for a candidate config that touches no state.

Touches: `src/maverick/engine.py`, `src/maverick/scheduling/scheduler.py`, `src/maverick/app.py`, `src/maverick/render/browser.py`, `src/maverick/ha/discovery.py`, `tests/test_runtime_displays.py (new)`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P1.2 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. `Engine.start` (src/maverick/engine.py lines 286 to 336) builds a transport, a lock and a state entry per display in a loop; `RenderScheduler.start` (src/maverick/scheduling/scheduler.py lines 80 to 106) adds one job per display and builds the `on_change` watch once from a fixed set (`_start_state_watch`, lines 161 to 187); `MqttDiscovery.announce` (src/maverick/ha/discovery.py line 65) announces every display. None of that is callable for a single display after startup, so the only way to apply a change is a restart, which relaunches Chromium and, with `render_on_start` on by default, re-renders every display at once. The pieces for the reverse direction partly exist: `MqttDiscovery.remove_display` (line 235) and `BrowserPool.drop_context` (src/maverick/render/browser.py line 134).

Build, with `Application` (src/maverick/app.py) as the orchestrator so the engine keeps knowing nothing about MQTT and the scheduler nothing about HTTP:
1. `Engine.register_display(display)` and `Engine.unregister_display(display_id)`. Register: append to `config.displays` by replacing the list so pydantic's `validate_assignment` re-runs `Config._unique_ids`; build and start the transport; create the lock and state entry. Unregister: take the display's lock so an in-flight render finishes; stop the transport; cancel nothing in the engine that belongs to the scheduler; delete the state entry and save state; remove the display's frames from disk (add `FrameStore.remove(display_id)` that deletes the three files); drop its browser contexts. Add `BrowserPool.drop_contexts_for(display_id)` that closes every context whose key starts with `f"{display_id}:"` (keys are built at src/maverick/render/dashboard.py line 263). Never stop the pool or the browser for any of these operations; assert that in a test.
2. `Engine.update_display(display)`: unregister the transport and contexts, keep the state entry (its counters and `frames_since_full` are still true of the panel), register the new config, and keep the persisted frame so a pull device is still served until the next render.
3. `RenderScheduler.add_display(display)`, `remove_display(display_id)` and `refresh_state_watch()`. Add and remove manage the job (`replace_existing` is already set), the `schedule_enabled` entry, and any pending debounce handle or task for that id. `refresh_state_watch` cancels the watch task and starts a new one from the current set of `on_change` entities, and is a no-op with a log line when `engine.ha` is None. After an add or an update with `render_on_start` true, schedule one render for that display only.
4. `Application.add_display(display)`, `update_display(display_id, display)`, `remove_display(display_id)` compose the above in this order: engine, scheduler, discovery (`announce_display` or `remove_display`), then the store (`DisplayStore.save`, from P1.1). If the store save fails, the runtime change stands and the failure is logged and raised so the API can report it; do not leave a display running that the file does not know about silently.
5. `Engine.render_candidate(display: DisplayConfig) -> RenderOutcome`: renders a config that need not be registered, using a throwaway context key it drops afterwards, runs the pipeline and the linter, never delivers, and never touches `states`, `frames` or the persisted state file. It reports lint findings rather than gating on them. This is what a Preview button will call.
6. Expose whether a display is rendering right now: an in-memory set the engine maintains around the locked section of `render`, read through `Engine.is_rendering(display_id)`.

Tests. tests/test_runtime_displays.py, built on the `make_engine` harness in tests/conftest.py (FakeTransport records deliveries; FakeRenderer stands in for Chromium) and on `Application` with a `RenderScheduler` running under pytest-asyncio. Cover: add starts a transport, creates the job (`scheduler.jobs()`) and renders once; update to a different transport type stops the old instance and starts a new one and does not call `BrowserPool.stop`; remove cancels the job, stops the transport, deletes the frame files and the state entry, and retracts discovery (extend the fake MQTT client from tests/test_mqtt_will.py to record the empty retained payloads); refresh_state_watch rebuilds the watched set; render_candidate leaves `states`, `frames` and `state.json` untouched and returns a frame with lint findings; a render in flight completes before remove returns. New user-facing messages go into docs/troubleshooting.md, as tests/test_troubleshooting_coverage.py requires.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

### P1.3 · REST endpoints for displays, preview and control

**Model:** Sonnet 5 at `high` · **Size:** Medium · **After:** P1.2

Create, replace, delete and preview a display over HTTP, pause its schedule, and make a render request return immediately when asked to. Everything a form needs to draw itself comes from the API.

Touches: `src/maverick/server/api.py`, `src/maverick/engine.py`, `docs/reference/http-api.md`, `README.md`, `tests/test_display_api.py (new)`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P1.3 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. src/maverick/server/api.py has two mutating routes, `POST /api/displays/{id}/render` (line 177) and `POST /api/render` (line 193), and both render. P1.2 added `Application.add_display`, `update_display`, `remove_display` and `Engine.render_candidate`; nothing reaches them over HTTP yet. The render route also blocks for the whole render, which under the default `render.timeout` of 45 s plus image and settle waits can pass a minute, and through ingress that request runs inside an iframe with no progress shown.

Add, all behind the existing `auth` dependency:
1. `GET /api/schema/display`: `DisplayConfig.model_json_schema()` plus a `transports` map of name to `{description, pushes, options}` where options is each transport's `options_doc` (src/maverick/transports/base.py). Every field description in the models is what the form will show as help text.
2. `POST /api/displays` with a `DisplayConfig` body: 201 and the display summary; 409 if the id exists. Let FastAPI's validation produce the 422; the `ConfigError`s raised inside validators are ValueErrors and surface as validation errors that name the key, which is the `extra="forbid"` invariant from CLAUDE.md doing its job. Add a test that `{"id": "x", "panell": "generic-mono"}` returns 422 whose body mentions `panell`.
3. `PUT /api/displays/{id}` full replacement; 400 if the body's id differs from the path. `DELETE /api/displays/{id}`: 204, and the frames are gone from disk.
4. `POST /api/displays/{id}/schedule` with `{"enabled": bool}`: runtime pause and resume through `scheduler.set_enabled`, publishing MQTT state the way `Application.handle_command` does for `schedule_on`/`schedule_off` (src/maverick/app.py lines 105 to 122). This is distinct from the config-level `enabled` flag, which goes through PUT.
5. `POST /api/displays/preview` with a `DisplayConfig` body: runs `render_candidate` and returns JSON with `preview_png` (base64), `width`, `height`, `lint` (summary, issues, metrics), `render_s` and `process_s`. No state changes; say so in the docstring.
6. `POST /api/displays/{id}/render?wait=false`: 202 with `{"display": id, "queued": true}` and the render runs as a task; if the display is already rendering, 202 with `"already_rendering": true`. The default `wait=true` keeps today's synchronous response for `rest_command` users.
7. `_display_summary` (api.py, around lines 124 to 166) gains `rendering` (from `Engine.is_rendering`), `next_run_at` (the job's `next_run_time` via `scheduler.jobs()`, src/maverick/scheduling/scheduler.py line 211, which nothing calls today), and the last render's durations. `DisplayState` (src/maverick/engine.py line 50) has no durations, so add `last_render_s` and `last_total_s`, set them in `Engine.render`, and publish them in the MQTT state payload where `render_duration` already comes from the outcome.

Documentation. docs/reference/http-api.md is hand-written prose (scripts/gen_docs.py docstring): add every route with parameters, responses and status codes, and regenerate openapi.json. The README's HTTP API table gains the new rows. CHANGELOG entry.

Tests. tests/test_display_api.py with TestClient on top of the P1.2 harness: every route above, the 422 naming test, the 409, the async render returning 202 and the summary showing `rendering` true while a slow FakeRenderer is held on an asyncio.Event, `next_run_at` present for an interval schedule and null for a manual-only display, and preview leaving the frame store empty.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

## Phase 2 — The setup UI

The page's own docstring says its purpose is the tuning loop: render, look, adjust, go again. Today every knob is YAML and every change is a restart. This phase turns the read-only status board into the place displays are created, tuned and watched, with no build step and no network dependency, because the app runs on a LAN that may have neither.

### P2.1 · UI shell: static assets, live status, in-place actions, layout fixes

**Model:** Opus 5 at `xhigh` · **Size:** Large · **After:** P1.3

Move the page's CSS and JS into served static files, render cards from the API, poll for state, stop reloading the page, and fix the phone layout. Every later UI prompt builds on this.

Touches: `src/maverick/server/ui.py`, `src/maverick/server/static/app.css (new)`, `src/maverick/server/static/app.js (new)`, `src/maverick/server/api.py`, `pyproject.toml`, `tests/test_setup_ui.py`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P2.1 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. src/maverick/server/ui.py is one Python file holding the CSS (`_CSS`) and JS (`_JS`) as strings and rendering the whole page server-side. The page has no polling or push, so a scheduled render in the background is invisible until a manual reload; the Refresh button waits on a synchronous POST and then calls `location.reload()` (line 106); the card grid's `minmax(340px,1fr)` (lines 35 to 36) inside 24 px padding makes phones narrower than about 390 px scroll sideways; the header says "MQTT off" with nothing to act on, while "Home Assistant not connected" gets a card; disabled displays render as ordinary cards; nothing shows when a panel will next update or how long the last render took, though P1.3 now reports both. The constraint in the module docstring stands: no build step, no dependencies, nothing fetched from a CDN, because this runs on a LAN that may have no internet.

Build.
1. Move the styles and script to src/maverick/server/static/app.css and app.js (a plain ES module, no framework). Serve them with Starlette's `StaticFiles` mounted at `/static`, referenced from the page as `static/app.js` (document-relative, for ingress). Add `server/static/*` to `[tool.setuptools.package-data]` in pyproject.toml (line 50; an earlier cleanup removed a non-existent entry there, so this one must be real) and prove they ship: build a wheel and list it in the PR description.
2. The page becomes a shell: the header, the link card when needed, an empty `<main>`, and the initial `GET /api/displays` JSON embedded in a `<script type="application/json">` block so the first paint has content with no spinner. app.js renders the cards from JSON on load and again from a poll of `api/displays` every 5 seconds, paused while `document.hidden`, updating cards in place rather than replacing the DOM. Send the token the way P0.2 established.
3. Card contents: name, id, panel and geometry line as today; status pill (rendering, ok, warnings, error, not rendered, paused, disabled); the preview image with cache-busting on checksum change only; next run as a relative time with the absolute time in a `title`; last render duration; transport and schedule summary; buttons Refresh and Full refresh that call `?wait=false` and show "Rendering…" from the `rendering` flag until the poll clears it; a Pause/Resume control on `POST .../schedule`; a disabled badge with an Enable action that does a PUT with `enabled` flipped; the ESPHome link; the lint findings list as today.
4. Header: two status chips. The MQTT chip, when off, opens a short explanation of what enabling MQTT gives (devices, buttons, the image entity) and how: in the app, install the Mosquitto broker app or set `mqtt_host`; standalone, `mqtt.enabled: true`. Keep the Home Assistant link card exactly as it is.
5. Layout: `grid-template-columns: repeat(auto-fill, minmax(min(340px, 100%), 1fr))`, a 16 px gutter at phone width, no horizontal scroll at 360 px. Keep the existing light and dark tokens. Buttons get `aria-busy` while waiting, visible focus states, and `prefers-reduced-motion` is respected.
6. `render_ui` keeps rendering the link card server-side, since it must work with no token and no JS.

Tests. tests/test_setup_ui.py checks two things that must survive: no root-relative `/api` paths, and every emitted `api/...` target resolves. Extend the scan to app.js (fetch targets there are the ones that matter now) and to `static/...` references, and add a test that `GET /static/app.js` and `/static/app.css` are served. The "broken credential still offers the link" tests must pass unchanged. Add a test that the embedded initial JSON is present and parses. No JavaScript test runner exists in the repository; keep app.js small and readable, and note in the PR that P4.3 adds the browser-level test.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

### P2.2 · The Add display form

**Model:** Sonnet 5 at `high` · **Size:** Medium · **After:** P2.1

The button that does not exist. A dialog driven entirely by the panel, transport and schema endpoints, posting to the create route and mapping validation errors back onto fields.

Touches: `src/maverick/server/static/app.js`, `src/maverick/server/static/app.css`, `src/maverick/server/ui.py`, `tests/test_setup_ui.py`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P2.2 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. There is no way to add a display from the page. The empty-state card (src/maverick/server/ui.py, the "No displays configured" section) tells the user to add a `displays:` entry to the file and restart. Everything a form needs already exists as JSON: `GET /api/panels` (src/maverick/server/api.py line 103, including geometry, colour scheme, dpi and notes), `GET /api/transports` (line 118), and `GET /api/schema/display` from P1.3 with every field's description and each transport's `options_doc`.

Build, in app.js and app.css from P2.1, using a native `<dialog>`:
1. An "Add display" button in the header and on the empty-state card, which now says what the button does instead of telling the user to edit a file.
2. Fields, in this order: Name, from which an id is derived as the user types (lower-case, `-` for spaces, matching the pattern `DisplayConfig._slug` enforces in src/maverick/config.py) and shown as an editable field with that rule as help text; Panel, a select grouped by vendor showing `width×height · scheme · dpi` per entry and the profile's notes under the select when one is chosen; Dashboard, a text field (P2.4 turns it into a picker) with the default `/lovelace/0` and the `DisplayConfig.dashboard` description as help; Transport, a select from `/api/transports`, and under it one field per key in that transport's `options_doc`, each with its description as help, plus a note that the `mqtt` transport needs MQTT enabled globally; Schedule: `every` and `cron` (mutually exclusive, with the model's own message when both are given), `quiet_hours`, `on_change` as a comma-separated entity list; an Enabled checkbox on by default. An "Advanced" disclosure holds the geometry overrides: width, height, colour scheme, dpi, rotation, frame format, each with its description and the panel's value as the placeholder.
3. Submit posts to `POST /api/displays`. A 422 is mapped onto the fields using pydantic's `loc`; a 409 marks the id field; a network error is shown in the dialog, not as an alert. On success the dialog closes, the new card appears on the next poll, and a "Render now" action is offered on it.
4. Keyboard: Escape closes, Enter submits from a text field, focus lands on Name when the dialog opens and returns to the button on close. The dialog must work at 360 px wide.

Tests. Extend tests/test_setup_ui.py: the page contains the dialog markup, and every endpoint the form's script references (`api/panels`, `api/transports`, `api/schema/display`, `api/displays`) resolves through the relative-path test. Add a server-side test that the empty-state card no longer tells the user to restart. Browser-level coverage of the form itself arrives with P4.3; say so in the PR.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

### P2.3 · Per-display editor with a dry-run preview

**Model:** Opus 5 at `xhigh` · **Size:** Large · **After:** P2.2

The tuning loop, in the page. Every field on the display model, generated from the schema with its description as help, a Preview that renders the candidate without saving, and Save and Delete.

Touches: `src/maverick/server/static/app.js`, `src/maverick/server/static/app.css`, `tests/test_display_schema.py (new)`, `tests/test_setup_ui.py`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P2.3 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. Roughly 110 fields across `theme`, `image`, `render`, `schedule`, `lint`, `transport`, `pack` and `esphome` (src/maverick/config.py, classes from line 274 to line 947) decide what a panel shows, and every one is YAML-only. The UI docstring (src/maverick/server/ui.py, top) says the page exists for the loop of rendering, looking, adjusting a threshold and going again; today "adjusting" means leaving the page, finding the file in another app, editing, restarting and coming back. P1.3 provides `GET /api/schema/display` with a description on every field and `POST /api/displays/preview`, which renders a candidate config and returns the PNG and lint report without saving anything.

Build, in app.js and app.css:
1. An Edit action on each card opening a side drawer (full-screen at phone width) with the display's current JSON loaded from `GET /api/displays/{id}` and the schema.
2. Sections generated from the schema, one per nested model, in this order: Display (name, dashboard, panel, enabled, geometry overrides), Schedule, Theme, Image, Render, Lint, Transport, Pack, ESPHome. Control by type: boolean to a switch; integer or number with `ge`/`le` to a number input with those bounds; enum to a select; string to a text input; `list[str]` to a comma-separated input; `palette_overrides` to a small table of name and RGB with a colour swatch; `extra_css` to a textarea. Every field shows its description as help. A field whose default is null and whose value comes from the panel profile (width, height, dpi, rotation, frame_format, colour scheme) shows the resolved value as its placeholder, taken from the summary's resolved fields. The Transport section shows the fields from that transport's `options_doc` and preserves any extra keys already in the config, because `TransportConfig` is the one `extra="allow"` model; every other section must send only known keys.
3. Preview: posts the current drawer state to `POST /api/displays/preview`, shows the returned PNG next to the display's current preview with a side-by-side and an overlay toggle, and lists the lint findings with their hints. It is clear in the UI that Preview saved nothing. A preview can take up to the render timeout; the button shows progress and the drawer stays usable.
4. Save does a PUT and closes on success; a 422 maps onto fields by `loc` and opens the section holding the first error. Delete asks for confirmation naming the display, then DELETE. A dirty drawer warns before closing. Escape closes.

Tests. tests/test_display_schema.py: walk `DisplayConfig` and every nested model and assert each field has a non-empty description (the editor shows it as help, and scripts/gen_docs.py relies on the same descriptions), that every enum the schema exposes has at least one value, and that the schema endpoint's transport map names every registered transport. Extend tests/test_setup_ui.py so the drawer's endpoints resolve. Browser-level coverage arrives with P4.3.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

### P2.4 · Dashboard picker from Home Assistant

**Model:** Sonnet 5 at `high` · **Size:** Medium · **After:** P2.2

The user types a dashboard path by hand and learns it was wrong after a render. Home Assistant can list its dashboards and their views over the WebSocket API the client already uses.

Touches: `src/maverick/ha/client.py`, `src/maverick/server/api.py`, `src/maverick/server/static/app.js`, `tests/test_ha_dashboards.py (new)`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P2.4 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. `HomeAssistantClient` (src/maverick/ha/client.py) has `check`, `call_service`, `get_state`, `set_state`, `fire_event` and `watch_states`, and nothing that lists dashboards. `DisplayConfig.dashboard` is typed free text with the default `/lovelace/0`. A wrong path is only discovered after a render, and the form from P2.2 cannot help.

Build.
1. Factor the one-shot WebSocket call out of `watch_states` (lines 175 to 236, which opens a `websockets` connection and authenticates with `_ws_authenticate`): a private `_ws_call(message_type, **payload)` that connects, authenticates, sends one command with an id, and returns the result or raises `HomeAssistantError` with the server's message. Reuse the token source and the same TLS handling.
2. `list_dashboards()` returning a list of `{url_path, title, views: [{path, title}]}`. Home Assistant's WebSocket API has a command that lists dashboards and one that returns a dashboard's configuration including its views; find their exact command types in the Home Assistant source (the lovelace component's websocket handlers) and cite the file in the docstring rather than recalling them. Include the default dashboard, whose url_path is `lovelace`. A dashboard whose config cannot be read (a YAML-mode dashboard, or one with no views) still appears, with an empty views list. Views yield paths of the form `/<url_path>/<view path or index>`.
3. `GET /api/ha/dashboards` behind auth, returning that list; 503 with a clear detail when `engine.ha_ok` is false.
4. In the Add form and the editor, the Dashboard field gains a `<datalist>` of the paths, each option's label being the dashboard and view titles, populated when the dialog opens and failing quietly to a plain text field when the endpoint is unavailable. Free text stays allowed: absolute URLs and `file://` pages are valid dashboards (see `resolve_url` in src/maverick/render/dashboard.py).
5. docs/guides/home-assistant.md: a paragraph on picking a dashboard, naming the endpoint. CHANGELOG entry.

Tests. tests/test_ha_dashboards.py: monkeypatch `_ws_call` to return fixture payloads shaped like the real responses (say in a comment which Home Assistant version they were taken from) and assert the list shape, the default dashboard's presence, the view path construction, and the empty-views fallback; test the endpoint's 503 when not connected. The client's new error message goes into docs/troubleshooting.md, as tests/test_troubleshooting_coverage.py requires.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

### P2.5 · Source screenshot next to the quantised frame

**Model:** Sonnet 5 at `high` · **Size:** Medium · **After:** P2.1

When a frame looks wrong, the question is whether the dashboard or the pipeline did it. The raw capture exists only on disk under `debug_artifacts`, and no route serves it.

Touches: `src/maverick/config.py`, `src/maverick/engine.py`, `src/maverick/server/api.py`, `src/maverick/server/static/app.js`, `docs/reference/configuration.md (generated)`, `tests/test_screenshot_route.py (new)`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P2.5 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. The pre-quantisation screenshot is written only when `render.debug_artifacts` is true, to `<data_dir>/debug/<id>/screenshot.png` (`Engine._write_debug`, src/maverick/engine.py line 596), and no route serves it. Seeing it means Samba or SSH. The UI shows the quantised frame alone, so "why does this look wrong" cannot be answered from the page.

Build.
1. `RenderConfig` (src/maverick/config.py line 517) gains `keep_screenshot: bool = True` with a description saying what is kept and where. Regenerate docs/reference/configuration.md.
2. After the pipeline runs in `Engine.render`, when the flag is set, downscale the raw render to the panel's resolution (the capture is at `supersample` times that size; the fit step in src/maverick/eink/pipeline.py already knows how to fit, so reuse it rather than re-implementing) and store it as PNG bytes in `FrameStore` alongside the frame, as a fourth file `<id>.screenshot.png`, persisted write-then-rename and loaded at startup like the others. Memory cost is one PNG at panel resolution per display; say so in the field description. `debug_artifacts` keeps writing the full-resolution capture as today.
3. `GET /api/displays/{id}/screenshot.png` with the same authentication as `preview.png` after P0.2, 404 before the first render.
4. `Engine.render_candidate` (P1.2) and `POST /api/displays/preview` (P1.3) return the downscaled screenshot as well, as `screenshot_png`, so the editor's preview can show source and result together.
5. UI: a two-way toggle over each card's image, Source and Frame, defaulting to Frame; the editor's preview pane gets the same toggle. Cache-bust both on checksum change.

Tests. tests/test_screenshot_route.py on the conftest harness: after a render the store holds the screenshot at panel resolution; the route serves it with `image/png`; it is 404 before the first render; with `keep_screenshot` false nothing is stored and the route stays 404; `FrameStore.load` restores it after a simulated restart; `FrameStore.remove` from P1.2 deletes it. Extend tests/test_setup_ui.py so the new relative target resolves.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

### P2.6 · Render history per display

**Model:** Sonnet 5 at `high` · **Size:** Medium · **After:** P2.1

`DisplayState` keeps the last error and counters. A panel that fails one render in ten has no trace of it after the next success.

Touches: `src/maverick/engine.py`, `src/maverick/server/api.py`, `src/maverick/server/static/app.js`, `docs/reference/http-api.md`, `tests/test_render_history.py (new)`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P2.6 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. `DisplayState` (src/maverick/engine.py line 50) holds `last_error`, `consecutive_failures` and counters. Once a render succeeds the previous failure is gone, and there is no record of what triggered each render, how long it took, whether it was skipped as unchanged or blocked by lint, or what the linter said. The MQTT state topic publishes one snapshot at a time for the same reason.

Build.
1. The engine keeps a bounded history per display: the last 50 outcomes, each `{at, trigger, ok, skipped, reason, render_s, process_s, total_s, lint_summary, checksum, full_refresh, delivery}`, appended in `Engine.render` wherever `_notify` is called so every path is recorded, including failures and skips. Persist to `<data_dir>/history/<id>.json` write-then-rename after each append, load at startup, and delete the file in `Engine.unregister_display` (P1.2). An unreadable file is logged and ignored, never fatal, matching `_load_state`.
2. `GET /api/displays/{id}/history?limit=N` behind auth, newest first, default 20, maximum 50.
3. UI: a History disclosure at the bottom of each card with a compact table: time (relative, absolute in title), trigger, outcome as a pill, duration, lint summary; failed and blocked rows are highlighted and their reason is shown on expand. It loads on open and refreshes with the poll while open.
4. docs/reference/http-api.md documents the route; docs/architecture.md's "State that survives a restart" table gains a row for history. CHANGELOG entry.

Tests. tests/test_render_history.py on the conftest harness: the buffer is bounded at 50; a failure, a lint block, an unchanged skip and a success each produce a row with the right fields; persistence round-trips through a new Engine on the same data_dir; removal deletes the file; the route honours limit and ordering.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

## Phase 3 — Pages and control

Roadmap decision 3, unbuilt. A display renders the one dashboard named in its config, and nothing can change it at runtime. Pages are how a panel shows the weather in the morning and the calendar in the evening, and they are the backend a control card needs.

### P3.1 · Pages: several dashboards per display, with rotation

**Model:** Opus 5 at `xhigh` · **Size:** Large · **After:** P2.3

An ordered page list per display with optional dwell times, page actions over HTTP and MQTT, a page picker in the UI, and the roadmap's proposed syntax made real.

Touches: `src/maverick/config.py`, `src/maverick/engine.py`, `src/maverick/scheduling/scheduler.py`, `src/maverick/app.py`, `src/maverick/ha/discovery.py`, `src/maverick/server/api.py`, `src/maverick/server/static/app.js`, `scripts/gen_docs.py`, `docs/roadmap.md`, `docs/architecture.md`, `tests/test_pages.py (new)`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P3.1 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. A display renders `DisplayConfig.dashboard` (src/maverick/config.py line 755 onwards) and nothing else. docs/roadmap.md, Decision 3, proposes a `pages:` list with dwell times and rotation plus `next_page`, `previous_page` and `set_page` actions, and marks the syntax as rejected by today's loader because `DisplayConfig` is `extra="forbid"`. docs/architecture.md lists "a display renders one dashboard" under known limitations.

Build.
1. Config: a `PageConfig(Base)` with `dashboard: str`, `name: str = ""` (derived from the dashboard path when empty), `dwell: str | float | None` (a duration, parsed like `schedule.every`), and `DisplayConfig.pages: list[PageConfig] = []` plus `rotate: bool = False`. `dashboard` remains the single-page shorthand; a validator refuses both a non-default `dashboard` and a non-empty `pages`. scripts/gen_docs.py requires every model reachable from `Config` to be claimed by a section (its module docstring); add PageConfig to the configuration page and regenerate. Add a commented example to config.example.yaml and the app's starter config.
2. Engine: `DisplayState` gains `page_index` and `page_shown_at`. `Engine.render` resolves the current page's dashboard at render time (`resolve_url` in src/maverick/render/dashboard.py takes the path). `Engine.set_page(display_id, index_or_name)`, `next_page`, `previous_page` update the state and trigger a render with trigger `page`. The unchanged-frame skip keeps comparing against the last delivered checksum, which is what a page change should beat.
3. Scheduler: when `rotate` is true, each scheduled tick advances to the next page if the current page's dwell has elapsed (or on every tick when dwell is unset), then renders. Use an injectable clock so the test can drive it.
4. API: `POST /api/displays/{id}/page` accepting `{"index": n}`, `{"name": "..."}` or `{"step": 1 | -1}`; the summary gains `page` (index, name, count).
5. MQTT: a `select` entity "Page" per display whose options are the page names, updated from state; commands `page:<name>`, `next_page`, `previous_page` on the existing command topic, routed in `Application.handle_command` (src/maverick/app.py line 105). `MqttDiscovery.remove_display` retracts the new entity too. docs/reference/mqtt.md is generated from the discovery code; regenerate.
6. UI: a page picker on the card (a select plus previous and next arrows) shown only when a display has pages, and a Pages section in the editor (P2.3) with add, remove and reorder.
7. Docs: move Decision 3's syntax from roadmap.md to architecture.md as implemented, mark the phase Done in the roadmap's sequence table, update the README's Roadmap excerpt and the guide on rendering when data changes. CHANGELOG entry.

Tests. tests/test_pages.py: config validation for the shorthand versus pages and for both given at once; page name derivation; rotation with dwell against a fake clock, including a dwell that spans several ticks and the wrap from the last page to the first; set_page by name and by index and the out-of-range error; the API route; MQTT command routing and the select discovery payload; the summary's page field. tests/test_docs_generated.py must stay green.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

## Phase 4 — Operations and the dev loop

Seven patch releases in two days, all packaging and authentication plumbing, each found in production. Part of that is self-inflicted: the app translates its options through bash into environment variables into YAML substitutions. Part is that nothing local reproduces the Supervisor, and nothing in the suite opens a browser. This phase is independent of Phases 1 to 3 and can run alongside them.

### P4.1 · Read the app's options in Python and shrink run.sh

**Model:** Opus 5 at `xhigh` · **Size:** Medium · **After:** nothing

The CLI already looks for `/data/options.json` and raises an error saying the add-on should have translated it. Doing the translation in Python removes the bashio semantics that caused three of the seven 0.2.x fixes.

Touches: `src/maverick/ha/options.py (new)`, `src/maverick/cli.py`, `src/maverick/ha/supervisor.py`, `app/run.sh`, `app/DOCS.md`, `.github/workflows/ci.yml`, `tests/test_app.py`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P4.1 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. app/run.sh reads the app's options through bashio, exports them as environment variables, and /config/maverick.yaml (written from app/rootfs/usr/share/maverick/maverick.yaml) reads them back through `${VAR}` substitution. Three of the 0.2.x fixes were consequences of that chain: bashio's `${2:-null}` turning an unset option into the string "null" (0.2.4, the `config_or_empty` helper), `${VAR:-default}` not substituting on an empty variable (0.2.1), and the argparse ordering of `-c` (0.2.2). Meanwhile src/maverick/cli.py lists `/data/options.json` in `DEFAULT_CONFIG_PATHS` (lines 20 to 24) only to raise "Found /data/options.json but no maverick.yaml. The add-on should have generated one" (lines 44 to 54). The service should read its own options.

Build.
1. New module src/maverick/ha/options.py: `load_app_options(path=Path("/data/options.json")) -> dict[str, str]` that maps the schema keys in app/config.yaml to exactly the environment variable names run.sh exports today (HA_URL, HA_TOKEN, HA_REFRESH_TOKEN, HA_CLIENT_ID, MAVERICK_LOG_LEVEL, MAVERICK_API_TOKEN, MAVERICK_BASE_URL, MQTT_ENABLED, MQTT_HOST, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD), treating a missing key, JSON null and the empty string all as unset. It reproduces run.sh's two derivations: MQTT precedence (explicit `mqtt_host` and friends, else the Mosquitto app's credentials from the Supervisor's `GET /services/mqtt`, else disabled with the same notice) and `base_url` (explicit option, else the host's first IPv4 address from `GET /network/info` with the prefix length stripped, else a warning). Both Supervisor calls go through `_request` in src/maverick/ha/supervisor.py (line 47), which already carries `SUPERVISOR_TOKEN`; `hassio_api: true` in app/config.yaml grants `/network/info` at the default role, and app/config.yaml's own comment cites `supervisor/api/middleware/security.py` for that. Check in that file whether `/services/mqtt` is reachable at the default role or whether the `services: mqtt:want` declaration is what grants it, and record the answer in the module docstring. Log the same informational lines run.sh logs today, including the "No Home Assistant credential yet" warning.
2. Apply the result by setting the variables into `os.environ` before `load_config` runs, so `${VAR}` in maverick.yaml keeps working and the starter config needs no migration. Call it from `cli._load` when `running_under_supervisor()` is true and the options file exists; the options.json branch that raises today goes away.
3. app/run.sh shrinks to: copy the starter config if /config/maverick.yaml is missing, `cd /config`, and `exec maverick -c /config/maverick.yaml serve --host 0.0.0.0 --port 5000`. Remove `config_or_empty` and the bashio option reads. Keep the file's header comment, rewritten to say the service reads its options itself.
4. tests/test_app.py: `test_every_option_run_sh_reads_is_declared` (line 105), `test_an_unset_option_is_never_read_with_an_empty_bashio_default` (113), `test_config_or_empty_turns_an_unset_option_into_an_empty_string` (133), `test_every_schema_key_is_translated` (196) and `test_template_variables_are_exported_by_run_sh` (201) encode the bash contract; replace them with the equivalent contract on the Python module: the set of keys it reads equals the schema keys; null, missing and "" are unset; every variable the starter config references is one the module sets; the derivations match, with the Supervisor calls monkeypatched. Keep `test_run_sh_invokes_the_cli_the_way_the_cli_parses` (73) and `test_the_manifest_can_reach_the_api_run_sh_derives_base_url_from` (167), renamed for the Python path.
5. CI (.github/workflows/ci.yml, the app job): keep the "Starter configuration loads in the image" step, and add one that runs the built image with a fake /data/options.json, a fake SUPERVISOR_TOKEN and no environment variables, and asserts the loaded config carries the option values. That step is the local reproduction the 0.2.x cycle lacked.
6. app/DOCS.md: the paragraph "The options do not reach the service directly" and the Options table intro; CONTRIBUTING.md's "The Home Assistant app" section; the run.sh docstring comments. The new module's messages go into docs/troubleshooting.md and the file into `SOURCE_FILES` of tests/test_troubleshooting_coverage.py. CHANGELOG entry. Do not move MAVERICK_REF: the next release does that, as CONTRIBUTING.md describes.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

### P4.2 · Standalone image and a local dev loop against a real Home Assistant

**Model:** Sonnet 5 at `high` · **Size:** Medium · **After:** nothing

The README says there is no standalone Docker image. Without one, and without a compose file that stands up Home Assistant and a broker next to Maverick, every packaging change is tested by releasing it.

Touches: `Dockerfile (new)`, `docker-compose.dev.yml (new)`, `dev/ (new: Home Assistant and Mosquitto seed config)`, `.github/workflows/ci.yml`, `README.md`, `CONTRIBUTING.md`, `docs/architecture.md`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P4.2 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. README.md's "Running as a service" section says there is no standalone Docker image, that app/Dockerfile is built by the Supervisor and expects the app's options, and that everywhere else means systemd. docs/architecture.md lists the missing image under known limitations. There is also no way to run Maverick against a real Home Assistant on a developer machine short of installing one by hand, which is why the app's packaging was debugged through seven releases.

Build.
1. A root Dockerfile modelled on app/Dockerfile (Debian bookworm base, a venv, Debian's chromium and the same three font packages, `MAVERICK_CHROMIUM_PATH=/usr/bin/chromium`, the same HEALTHCHECK) that installs the package from the build context (`COPY . /src` then `pip install /src`) rather than from a git ref, and runs `maverick -c /config/maverick.yaml serve --host 0.0.0.0 --port 5000` with `/config`, `/media` and `/share` as volumes. It must not read /data/options.json or assume a Supervisor. Keep app/Dockerfile separate and unchanged: CONTRIBUTING.md explains why it installs from MAVERICK_REF.
2. docker-compose.dev.yml with three services: `homeassistant` from `ghcr.io/home-assistant/home-assistant:stable` with a seeded `dev/homeassistant/` config volume containing a minimal configuration.yaml, a demo dashboard with a few cards, and onboarding pre-completed if the image allows it (document what the developer still has to do in the browser on first run: create the user, make a long-lived token); `mosquitto` from `eclipse-mosquitto` with a `dev/mosquitto.conf` allowing anonymous connections on the dev network; `maverick` built from the root Dockerfile with `dev/maverick.yaml` mounted at /config/maverick.yaml, pointing at `http://homeassistant:8123`, reading the token from `HA_TOKEN` in the environment, MQTT enabled against `mosquitto`, `server.base_url` set to what a panel on the host would use, and the source tree bind-mounted with `pip install -e` so a code change needs only `docker compose restart maverick`.
3. A short `scripts/dev.sh` (or Makefile targets, whichever CONTRIBUTING.md's style prefers) with `up`, `down`, `logs`, `render <id>` and `check`.
4. CI: a job that builds the root image on pull requests (amd64) and runs the same three smoke commands the app job runs (version, panels, a BrowserPool launch). Do not push images; there is no registry yet.
5. Docs: README "Running as a service" gains a Docker section before the systemd one, and the "no standalone Docker image" sentence goes; docs/architecture.md drops that known limitation; CONTRIBUTING.md gains "Running against a real Home Assistant" describing the compose loop and what it does and does not reproduce (it is not the Supervisor: no ingress, no options.json, no bashio). docs/roadmap.md's Decision 1 paragraph that says a standalone Dockerfile does not exist is updated. Every claim names its file. CHANGELOG entry.

Tests. Add to tests/test_app.py, or a new tests/test_docker.py, assertions that the root Dockerfile installs from the build context and not from a URL, sets the chromium path, declares the three volumes, and that docker-compose.dev.yml parses as YAML and names the three services. The image build itself is CI's job; nothing in the suite may need Docker.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

### P4.3 · Browser regression tests for the renderer and the UI

**Model:** Sonnet 5 at `high` · **Size:** Medium · **After:** P2.2

Nothing in the suite opens a browser, so the two prototype findings the architecture doc calls the ones that break home-grown tools have no regression test. The UI forms from Phase 2 have none either.

Touches: `tests/browser/ (new)`, `tests/browser/fixtures/ (new static pages)`, `.github/workflows/ci.yml`, `pyproject.toml`, `CONTRIBUTING.md`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P4.3 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. tests/conftest.py stubs the renderer and the transports, so `pytest -q` never opens a browser, and CI (.github/workflows/ci.yml) deliberately runs no `playwright install`. docs/architecture.md, "What a prototype proved", says the two findings that break home-grown tools, seeding the auth bundle before the first script and adopting the stylesheet into every shadow root, "need a browser to fail in, and nothing in the suite stands in for one". The UI from Phase 2 (a form, an editor, polling) has no browser-level test either. The parts of P4.3 that test the renderer do not depend on Phase 2; the UI part does.

Build.
1. A `browser` pytest marker registered in pyproject.toml, with `pytest -q` excluding it by default (`-m "not browser"` in addopts) and a conftest in tests/browser/ that skips the whole directory unless Chromium is available: Playwright's own download or `MAVERICK_CHROMIUM_PATH`, the same rule `BrowserPool` uses (src/maverick/render/browser.py lines 44 to 62). CI gains a job that runs `playwright install chromium --with-deps` and `pytest -m browser`.
2. Fixture pages under tests/browser/fixtures/, served from a `http.server` thread on a free port: `dashboard.html` with a `<home-assistant>` custom element that attaches a shadow root, a card inside it that attaches its own shadow root 500 ms after load, some text, and an `<img>` that loads slowly; `login.html` whose body contains an `<ha-authorize>` element. No Home Assistant is involved.
3. Renderer tests, driving `DashboardRenderer` from src/maverick/render/dashboard.py with a real `BrowserPool` and a `HomeAssistantConfig` whose url points at the fixture server, a `token` set, and `render.wait_for_selector` set to `home-assistant`: (a) after the render, evaluating in the page shows the injected stylesheet in `adoptedStyleSheets` of the document and of both shadow roots, including the late one; (b) `localStorage.hassTokens` exists before the page's own first script runs, with `hassUrl` equal to `render_url` (have the fixture page record what it saw at load time into a global and read it back); (c) rendering `login.html` raises `RenderError` with the login-form message and drops the context (`BrowserPool.drop_context` is called; observe it through the pool's context dict); (d) the returned image is the viewport size times `supersample`.
4. UI test, only if Phase 2 has landed (skip with a reason otherwise): start the FastAPI app under uvicorn in a thread on a free port with the conftest `minimal_config` (fake transport) and the FakeRenderer monkeypatched in, open `/` with Playwright, add a display through the form, see its card appear, press Refresh, and see the status pill pass through rendering to ok. Keep this test under 30 seconds.
5. CONTRIBUTING.md "Running the checks" gains the browser suite and how to run it locally; docs/architecture.md's two "not yet tested" notes become "Verified:" notes naming the tests, in the style the page already uses. CHANGELOG entry.

Everything under tests/browser/ must be skipped, not failed, in a bare checkout, and `pytest -q` with no marker must not get slower.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

## Phase 5 — Reconcile the documentation

Every prompt above updates the pages it touches. This one reads the whole documentation set against the code as it then is and fixes what drifted, and it re-sequences the roadmap so it describes the product that now exists.

### P5.1 · Reconcile the docs and re-sequence the roadmap

**Model:** Sonnet 5 at `high` · **Size:** Medium · **After:** P2.6, P3.1, P4.3

Known limitations that are now false, a roadmap that still puts UI setup behind an integration, install steps that say edit the file and restart, and a changelog ready for 0.3.0.

Touches: `docs/architecture.md`, `docs/roadmap.md`, `README.md`, `app/DOCS.md`, `docs/guides/home-assistant.md`, `docs/troubleshooting.md`, `docs/README.md`, `CHANGELOG.md`

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, FastAPI, pydantic v2, Playwright). Before changing anything, read CLAUDE.md and CONTRIBUTING.md and follow them: British spelling in prose (colour, grey, quantise, catalogue); never hand-edit docs/reference/ (change the model or the _*.md include, run `python scripts/gen_docs.py`, commit what it writes); every documentation claim names the source file it came from; the hardware-untested banner stays on every page that describes a device path; add an entry under [Unreleased] in CHANGELOG.md in the style of the existing ones; do not bump the version and do not move MAVERICK_REF in app/Dockerfile (releasing is a separate procedure in CONTRIBUTING.md). This task is P5.1 in docs/implementation-plan.md; read that entry first if the file exists. Line numbers below refer to commit 29f95e1; re-locate them if the files have moved on.

Problem. The documentation set was written for a service whose displays came from a file. After Phases 0 to 4: docs/architecture.md's "Known limitations" still lists "the setup UI cannot change a display", "a display renders one dashboard" and "there is no standalone Dockerfile"; docs/roadmap.md's sequence table still puts "UI setup instead of YAML" inside the integration phase and marks pages "not started"; README.md's app install steps end with "Edit `displays:` in that file and restart the app", and its Status section says version 0.1.0; app/DOCS.md says the same about editing the file; docs/guides/home-assistant.md has no mention of the UI, the dashboard picker or pages; docs/README.md indexes no page about the UI.

Do, reading each page against the code rather than against this list:
1. docs/architecture.md: remove the limitations that are now false and add sections for the display store and precedence rule, the runtime lifecycle, the dry-run preview, history, the screenshot store and pages, each naming its source file and its test in the "Verified:" style the page already uses. Update "Last reviewed against commit".
2. docs/roadmap.md: rewrite the sequence table so phase 2 reads as "UI setup: done without the integration; the integration remains for devices and actions without a broker and a config flow", phase 3 as done, and re-estimate what is left. Keep "Still open" honest: hardware validation has still not happened. Update "Last reviewed against commit".
3. README.md: Status (the version, what now works, the hardware banner unchanged), the app install steps (step 5 becomes "open the Web UI and add a display"), Quick start (offer the UI path first for app users and keep the CLI loop for standalone users), Configuration (the displays store), HTTP API table (every new route with its token column), Roadmap excerpt.
4. app/DOCS.md: Install, The configuration file, Files and folders (displays.yaml, history/, screenshots), Troubleshooting for the new messages; "What is not there yet" trimmed to what is true.
5. docs/guides/home-assistant.md: a section on the setup UI, the dashboard picker and pages; docs/README.md indexes it. docs/troubleshooting.md: tests/test_troubleshooting_coverage.py will have kept every message documented, but read the page as a user would and group the new entries sensibly.
6. CHANGELOG.md: consolidate [Unreleased] into a release note shaped for 0.3.0, grouped Added, Changed, Fixed, with each entry naming the pull request. Do not cut the release; CONTRIBUTING.md's procedure does that.
7. Every claim names a source file; British spelling; the hardware-untested banner stays on every device-path page; `python scripts/check_links.py` and `python scripts/gen_docs.py --check` pass.

Before you finish: `ruff check` (the whole tree, including scripts/), `pytest -q`, `python scripts/gen_docs.py --check` and `python scripts/check_links.py` must all pass, and the suite must still run with no Chromium, no broker and no Home Assistant instance, as tests/conftest.py promises. Re-read your own diff as a reviewer would and fix what you find. Commit with a clear message and open a pull request whose description says what changed, why, and how it was verified.
```

## Sequence

Within a phase the order is the order shown. Across phases, Phase 0 first, Phase 4 alongside anything, Phase 5 last.

| Prompt | Start | Can run alongside |
| --- | --- | --- |
| P0.1 | first, alone | Nothing |
| P0.2 | after P0.1 | P1.1, P4.1, P4.2 |
| P1.1 | any time after P0.1 | P0.2, P4.1, P4.2 |
| P1.2 | after P1.1 | P4.1, P4.2 |
| P1.3 | after P1.2 | P4.1, P4.2 |
| P2.1 | after P1.3 | P4.1, P4.2 |
| P2.2 | after P2.1 | P2.5, P2.6, P4.x |
| P2.3 | after P2.2 | P2.4, P2.5, P2.6, P4.3 |
| P2.4 | after P2.2 | P2.3, P2.5, P2.6, P4.3 |
| P2.5 | after P2.1 | P2.2 onwards |
| P2.6 | after P2.1 | P2.2 onwards |
| P3.1 | after P2.3 | P2.4, P2.5, P2.6, P4.x |
| P4.1 | any time after P0.1 | Everything in Phases 1 to 3 |
| P4.2 | any time after P0.1 | Everything in Phases 1 to 3 |
| P4.3 | renderer half any time; UI half after P2.2 | P2.3 onwards |
| P5.1 | last | Nothing |

## After this plan

These stay on [roadmap.md](roadmap.md). None was in the review, and none is needed for the product to be usable; they are what comes next.

- **Hardware validation** (roadmap phase 1). Nothing here has run on a panel. The banner stays until a contributor reports a successful run with the panel id and firmware version.
- **A custom integration** (the rest of roadmap phase 2): devices and actions without a broker, and a config flow. After this plan it is a convenience rather than the only way to set up a display.
- **A dashboard strategy and an editing theme** (roadmap phase 4), so an e-ink-correct dashboard is generated rather than authored.
- **A template source** (roadmap phase 5): HTML and Jinja as a display's source. The renderer already loads any URL, so this is templating and plumbing.
