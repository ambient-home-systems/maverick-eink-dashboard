# Maverick — Documentation Review and Plan

> **Interactive version with copy buttons:** https://claude.ai/code/artifact/f5bfdfe0-9e26-45e5-9cfb-df17dbc4025c
>
> Reviewed 14 September 2026 against commit `4b55146` (branch `main`, 36 source files, 6,970 lines). Line numbers in the findings refer to that commit.

## Where it stands

**Implemented.** Every prompt below was run and merged between 14 September 2026 and 15 September 2026, as [PRs #3 to #17](https://github.com/ambient-home-systems/maverick-eink-dashboard/pulls?q=is%3Apr+is%3Amerged), one per prompt plus one follow-up (#12, the four gaps the design guide recorded). A seventh phase that was not in the plan, the Home Assistant app and the README's install button, merged as [#18](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/18). `main` is at `4d8c046`. Each finding names the PR that resolved it, and each prompt the PR it produced.

## Verdict

The repository holds about 7,000 lines of working service code and one document, and the document describes a future product rather than the software that exists. There is no README, so the GitHub page and the PyPI metadata are empty; a user who installs it must read source to learn the config keys, the transports and the CLI. The architecture evaluation is good writing that now disagrees with the code on its status, on the Home Assistant surface and on one YAML example the config rejects. Four source strings point users at commands and files that do not exist, and checking the claims the new docs would make surfaced two real bugs.

The fix is not one large README. It is a small generator so the reference pages are derived from the code and checked in CI, four guides, a split of the architecture document into built and planned, and contributor rules that keep it that way. 29 findings; 14 prompts across six phases, each written for a fresh Claude Code session.

## What exists today

| Artifact | State | Notes |
|---|---|---|
| `README.md` | Missing | pyproject.toml:9 points at it (`readme = "README.md"`). The built package has an empty long description (checked after `pip install -e .`). The setup UI's "See the docs" link (server/ui.py:163) sends people to the repository root, which has nothing to read. |
| `docs/architecture.md` | Present, stale | 216 lines. A feasibility evaluation: verdict, four prototype findings, three product decisions, risks, an 8–12 week sequence. Well written. Describes a design, not the ~7,000 lines of code that now sit next to it. |
| `docs/architecture.html` | Present, diverged | 551 lines. A hand-maintained styled twin of the Markdown. Already differs: the Markdown has a References list and a "Shareable version" link (line 3); the HTML has neither and adds a colophon (line 543). Its status badge says "Not built · no hardware testing" (line 200). |
| `config.example.yaml` | Missing | `maverick init` looks for it in the package and at the repo root (cli.py:273, 275) and falls back to a 15-line inline stub with four keys. |
| `docs/design-guide.md` | Missing, but linked | The linter's hairline warning tells the user to read it (eink/lint.py:173). |
| `docs/ recipes` | Missing, but linked | transports/pull.py:15 refers to "the ESPHome and Kindle recipes in docs/". |
| `tests/, .github/` | Missing | pyproject declares `testpaths = ["tests"]` (line 54) and dev extras (pytest, pytest-asyncio, ruff) with nothing to run and no CI. |
| `Dockerfile, add-on` | Missing | The CLI already looks for `/config/maverick.yaml` and `/data/options.json` (cli.py:20–23) and config.py:87 says values are "injected automatically" inside the add-on. No add-on exists. |
| `CONTRIBUTING.md, CLAUDE.md, CHANGELOG.md` | Missing |  |

## Findings

Severity: **Blocker** stops a new user; **High** costs most users time; **Medium** costs some; **Low** is hygiene. **Bug** and **Minor** in group D are code defects the documentation would expose.

### A. Missing documentation

*What a user cannot find out today without reading the source.*

- **A1 · Blocker · No README and no install path.** Python ≥ 3.11, twelve dependencies, Chromium via `playwright install chromium`, and on aarch64 a distro Chromium pointed to by `MAVERICK_CHROMIUM_PATH` (Playwright ships no ARM Linux build). None of it is written down anywhere. Evidence: `pyproject.toml:9`, `render/browser.py:51–56`. *Resolved in [#5](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/5).*
- **A2 · High · No configuration reference.** Twelve pydantic models and roughly 110 fields across `home_assistant`, `mqtt`, `server` and `displays[]` (theme, image, render, schedule, transport, pack, esphome). `${VAR}` and `${VAR:-default}` substitution, duration strings like `5m`, and `extra="forbid"`, which turns a misspelt key into a hard error. The only sample is the four-key stub. Evidence: `config.py`, `config.py:76`. *Resolved in [#6](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/6).*
- **A3 · High · Transport options are undocumented and unvalidated.** `TransportConfig` is `extra="allow"`, so each transport's keys exist only where `self.option(...)` is called: opendisplay has twelve (mode, device_id, media_dir, media_root, media_source_prefix, rotation, mac, device_name, encryption_key, timeout, max_attempts, scan_timeout), webhook four, file three, mqtt one, http_pull one (`mac`, for TRMNL). Evidence: `config.py:261`, `transports/*.py`. *Resolved in [#6](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/6), [#7](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/7).*
- **A4 · High · No Home Assistant guide.** The MQTT-discovery device (two buttons, a switch, an image, five sensors, a binary sensor), the command payloads (`refresh`, `full_refresh`, `schedule_on`, `schedule_off`), the `rest_command` alternative, how to make the long-lived token, why `home_assistant.url` must match the frontend origin exactly, and the `mode: ha` prerequisites for OpenDisplay (device registry id, a writable `/media` path) all live in docstrings. Evidence: `ha/discovery.py:1–22`, `app.py:84`, `render/dashboard.py:9`, `transports/opendisplay.py:9`. *Resolved in [#9](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/9).*
- **A5 · High · No device recipes, though the code promises them.** The ESPHome generator, the TRMNL bring-your-own-server handshake (`/api/setup`, `/api/display`), the Kindle and Kobo PNG path and the Inky-over-MQTT path each need a page. There is also no example client for the MQTT frame payload. Evidence: `transports/pull.py:15`, `server/api.py:246`, `esphome/generator.py:183`. *Resolved in [#10](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/10).*
- **A6 · Medium · No panel catalogue page.** 28 profiles across eight vendors, visible only through `maverick panels`. The meaning of `dpi`, `native_rotation`, `supports_partial` and `full_refresh_every` is in YAML comments and dataclass fields. Evidence: `devices/panels.yaml`, `devices/profiles.py`. *Resolved in [#7](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/7).*
- **A7 · Medium · No HTTP API reference outside the live /api/docs.** The pull protocol the architecture document calls "the design" (ETag and 304, `X-Maverick-Next-Refresh`, the Date-header contract) is documented in code comments only. Thirteen routes, none listed anywhere static. Evidence: `server/api.py:206`, `server/api.py:8`. *Resolved in [#8](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/8).*
- **A8 · Medium · No design guide, though the linter cites one.** The thresholds (blank 99.5 %, ink 62 %, hairline 28 %, speckle 3.5 %, spot ink 18 %, 0.18 mm minimum feature) and the theme rules (3.2 mm body text, weight floor 400, 0.25 mm rules, zero radius) are the product's real design system and exist only as dataclass defaults. Evidence: `eink/lint.py:69–83`, `eink/theme.py:59–95`, `eink/lint.py:173`. *Resolved in [#11](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/11), [#12](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/12).*
- **A9 · Medium · No troubleshooting page.** The code carries 48 user-facing failure messages (14 ConfigError, 18 DeliveryResult.failure, 7 HomeAssistantError, 6 RenderError, 3 RuntimeError), and they are good ones. No page lists them with their fixes. Evidence: `grep across src/maverick`. *Resolved in [#13](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/13).*
- **A10 · Low · No contributor documentation.** No CONTRIBUTING, no CLAUDE.md, no test or lint instructions, no CI, no changelog. Evidence: `repository root`. *Resolved in [#16](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/16), [#17](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/17).*

### B. The architecture document is out of step with the code

*Good writing that now describes a different repository.*

- **B1 · High · Its status is wrong.** The Markdown says "Status: feasibility evaluation … nothing has been tested against real display hardware"; the HTML badge says "Not built". The repository contains the render service the document estimates at "3–5 weeks": pipeline, five transports, scheduler, HTTP server, CLI, MQTT discovery, ESPHome generator. A reader cannot tell what exists. Evidence: `docs/architecture.md:5`, `docs/architecture.md:194`, `docs/architecture.html:200`. *Resolved in [#14](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/14).*
- **B2 · High · Decision 1 contradicts the implementation.** "This supersedes the earlier MQTT-discovery proposal, which becomes a fallback." MQTT discovery is the only Home Assistant surface in the code, plus one REST endpoint. The `maverick.render` and `set_page` actions, the config flow, HACS distribution and ingress do not exist. Evidence: `docs/architecture.md:91`, `ha/discovery.py`. *Resolved in [#14](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/14).*
- **B3 · Medium · Decision 3 shows YAML the config rejects.** The `pages:` example would fail to load: `DisplayConfig` is `extra="forbid"`, so an unknown key is a ConfigError. Nothing marks the block as proposed syntax. Evidence: `docs/architecture.md:156`, `config.py:76`, `config.py:293`. *Resolved in [#14](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/14).*
- **B4 · Medium · Two hand-maintained copies, one private link.** The Markdown and HTML are edited separately and have already diverged. The Markdown's canonical link is a claude.ai artifact URL, private to its owner and not a durable public reference for a repository. Evidence: `docs/architecture.md:3`, `docs/architecture.html:543`. *Resolved in [#14](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/14).*
- **B5 · Medium · "Verified" findings are not reproducible.** The four prototype findings cite measurements (13 px text at 3.03 mm @124 dpi, speckle ratio 0.000) with no test in the repository that produces them. The code paths exist (`build_css` zoom, the AUTO dither mask, the `blank_render` gate), so they can become tests. Evidence: `docs/architecture.md:34–63`, `eink/theme.py:109`, `eink/lint.py:69`. *Resolved in [#15](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/15).*
- **B6 · Low · Undated, unversioned, thinly referenced.** No date, version or commit. "As of 2026.5" (strategy registration in Home Assistant) is uncited. The references omit the three integration targets the code depends on: ESPHome `online_image`, the TRMNL BYOS API and py-opendisplay. Evidence: `docs/architecture.md:119`, `docs/architecture.md:208–216`. *Resolved in [#14](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/14).*
- **B7 · Low · The best troubleshooting content is filed as risk.** The Risks table (login-page frame on a battery panel, Chromium on ARM, the ESP32 memory ceiling, muddy colour) is user guidance framed as project risk. The troubleshooting page should absorb it. Evidence: `docs/architecture.md:170–178`. *Resolved in [#13](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/13).*

### C. Source strings that point at things that do not exist

*Drift a user hits the first time something goes wrong.*

- **C1 · High · Hint names a flag that does not exist.** The `blank_render` hint says "Run `maverick render --debug`". There is no `--debug` flag. The real switch is `render.debug_artifacts: true` in the display config, which writes `screenshot.png`, `frame.png` and `lint.json` under `data_dir/debug/<id>/`. Evidence: `eink/lint.py:132`, `cli.py:322`, `config.py:201`. *Resolved in [#3](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/3).*
- **C2 · Medium · Wrong subcommand in an error message.** "Run `maverick opendisplay scan`". The command is `maverick scan`. Evidence: `transports/opendisplay.py:152`, `cli.py:341`. *Resolved in [#3](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/3).*
- **C3 · Medium · Docstring promises recipes that are not there.** "This is how the ESPHome and Kindle recipes in `docs/` stay small." Evidence: `transports/pull.py:15`. *Resolved in [#3](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/3).*
- **C4 · Medium · Linter links a missing page.** "see docs/design-guide.md". Evidence: `eink/lint.py:173`. *Resolved in [#3](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/3).*
- **C5 · Low · Package data lists four paths that do not exist.** `eink/*.css`, `esphome/templates/*.j2`, `server/static/*`, `server/templates/*`. Harmless at build time, misleading to a contributor looking for missing assets. Evidence: `pyproject.toml:50`. *Resolved in [#3](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/3).*
- **C6 · Low · A default for a transport that is not registered.** `_default_format_for` lists an `esphome` transport. The registry has mqtt, opendisplay, http_pull, file and webhook; `transport.type: esphome` fails at start with "Unknown transport". Evidence: `config.py:377–385`, `transports/__init__.py:13–15`. *Resolved in [#3](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/3).*
- **C7 · Low · Docstring describes an add-on that does not exist.** "Inside the add-on both values are injected automatically, so users never see them." Evidence: `config.py:87`. *Resolved in [#3](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/3).*
- **C8 · Low · "See the docs" goes nowhere useful.** The empty-state card in the setup UI links to the repository root. Evidence: `server/ui.py:163`. *Resolved in [#3](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/3).*

### D. Behaviour the new documentation would advertise, and that is wrong today

*Found while checking claims against code. Fix these before writing them down.*

- **D1 · Bug · The MQTT last will is never registered.** `Application.start` sets `mqtt._will` after `Engine.start` has already created and connected the publisher; `MqttPublisher.start` is the only caller of paho's `will_set` and returns early once connected. The discovery docstring's promise that "a last-will marks them unavailable if Maverick dies" is false: entities stay online after a crash. Evidence: `app.py:45`, `engine.py:295–297`, `transports/mqtt.py:45`, `transports/mqtt.py:56`, `ha/discovery.py:21`. *Resolved in [#4](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/4).*
- **D2 · Bug · TRMNL onboarding breaks when server.api_token is set.** `/api/setup` hands the token back as `api_key` and an `image_url` with no token in it. The frame endpoint accepts only `Authorization: Bearer` or `?token=`, which TRMNL firmware does not send, so every fetch is a 401. Evidence: `server/api.py:260`, `server/api.py:317`, `server/api.py:32–40`. *Resolved in [#4](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/4).*
- **D3 · Minor · POST /api/render?force=true is accepted and ignored.** The parameter is declared, so /api/docs advertises it, and then dropped on the way to `Application.render_all`. Evidence: `server/api.py:174–175`. *Resolved in [#4](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/4).*
- **D4 · Minor · palette_overrides reaches quantisation but not the stylesheet.** `build_css` calls `get_palette(options.scheme)` without the overrides, so a corrected red ink changes the dither but not the CSS accent. The reference must describe the real behaviour, or the code should pass the overrides through. Evidence: `eink/theme.py:109`, `eink/pipeline.py`. *Resolved in [#4](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/4).*

## The plan

Six phases, genuinely ordered: each assumes the previous one is merged. Within a phase, prompts marked *parallel* can run at the same time. Each card names the model and the effort level to run it at, and why. Size: S is one or two files; M is several files; L is a new page set or subsystem.

| Phase | Prompt | Model | Effort | Size | Parallel | Merged as |
|---|---|---|---|---|---|---|
| 0 · Stop the drift | P0.1 Fix the source strings that point at things that do not exist | Claude Sonnet 5 (`claude-sonnet-5`) | medium | S | yes | [#3](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/3) |
| 0 · Stop the drift | P0.2 Verify and fix three behaviours the documentation will advertise | Claude Opus 5 (`claude-opus-5`) | high | M | yes | [#4](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/4) |
| 1 · The front door | P1.1 README and config.example.yaml | Claude Opus 5 (`claude-opus-5`) | high | M | no | [#5](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/5) |
| 2 · Reference, generated from the code | P2.1 Documentation generator and the configuration reference | Claude Opus 5 (`claude-opus-5`) | high | L | no | [#6](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/6) |
| 2 · Reference, generated from the code | P2.2 Panel catalogue, transports and CLI reference pages | Claude Sonnet 5 (`claude-sonnet-5`) | medium | M | yes | [#7](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/7) |
| 2 · Reference, generated from the code | P2.3 HTTP API and MQTT reference | Claude Opus 5 (`claude-opus-5`) | high | M | yes | [#8](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/8) |
| 3 · Guides | P3.1 Home Assistant guide | Claude Opus 5 (`claude-opus-5`) | high | M | yes | [#9](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/9) |
| 3 · Guides | P3.2 Device recipes | Claude Opus 5 (`claude-opus-5`) | high | L | yes | [#10](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/10) |
| 3 · Guides | P3.3 E-ink design guide | Claude Opus 5 (`claude-opus-5`) | xhigh | M | yes | [#11](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/11), [#12](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/12) |
| 3 · Guides | P3.4 Troubleshooting | Claude Sonnet 5 (`claude-sonnet-5`) | medium | M | yes | [#13](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/13) |
| 4 · The architecture document | P4.1 Refresh the architecture document: built versus planned | Claude Opus 5 (`claude-opus-5`) | xhigh | L | yes | [#14](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/14) |
| 4 · The architecture document | P4.2 Make the "verified" claims reproducible | Claude Opus 5 (`claude-opus-5`) | high | M | yes | [#15](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/15) |
| 5 · Contributors and upkeep | P5.1 Contributor docs, CLAUDE.md and documentation CI | Claude Opus 5 (`claude-opus-5`) | high | M | no | [#16](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/16) |
| 5 · Contributors and upkeep | P5.2 Changelog, single-sourced version and review stamps | Claude Sonnet 5 (`claude-sonnet-5`) | low | S | no | [#17](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/17) |
| 6 · Install from the app store | done in the session, no prompt | — | — | L | — | [#18](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/18) |

### Phase 0 — Stop the drift

Two small code changes before any prose is written, so nothing documented is already wrong. Fixes C1–C8 and D1–D4.

#### P0.1 — Fix the source strings that point at things that do not exist

**Model:** Claude Sonnet 5 (`claude-sonnet-5`, alias `sonnet`) · **Effort:** medium · **Size:** S · **Parallel:** yes · **Merged as:** [#3](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/3)  
*Why this model and effort:* Mechanical edits with an exact list; a mid-effort Sonnet 5 run keeps it cheap and fast.

```text
You are working in the maverick-eink-dashboard repository (Python 3.11, package under src/maverick). Create a branch `docs/p0-drift-fixes` from main.

Goal: remove every user-facing string, docstring and packaging entry that refers to a command, flag, file or path that does not exist. Do not add features. Do not change behaviour.

Fix exactly these, reading the surrounding code before each edit:

1. src/maverick/eink/lint.py:132 — the blank_render hint says "Run `maverick render --debug`". There is no --debug flag (see build_parser in src/maverick/cli.py). Reword to: set `render.debug_artifacts: true` on the display and re-run `maverick render <id>`; the raw screenshot is written to `<data_dir>/debug/<id>/screenshot.png` (see Engine._write_debug in src/maverick/engine.py).
2. src/maverick/transports/opendisplay.py:152 — says "Run `maverick opendisplay scan`". The command is `maverick scan` (cli.py, `scan = sub.add_parser`).
3. src/maverick/transports/pull.py:15 — refers to "the ESPHome and Kindle recipes in ``docs/``". They do not exist yet. Remove the sentence; a later change adds docs/recipes/ and will restore a correct reference.
4. src/maverick/eink/lint.py:173 — "see docs/design-guide.md". Keep the path and create docs/design-guide.md as a stub: a title, one paragraph saying the full guide is being written, and a bullet list of the LintThresholds defaults with their meaning copied from the dataclass comments at lint.py:69-83.
5. src/maverick/config.py:87 — the HomeAssistantConfig docstring says "Inside the add-on both values are injected automatically". There is no add-on. Say what is true: the values come from the config file, or from `${HA_TOKEN}`-style environment substitution.
6. src/maverick/config.py:377-385 — `_default_format_for` lists an "esphome" transport. No transport of that name is registered (src/maverick/transports/__init__.py registers mqtt, opendisplay, http_pull, file, webhook). Remove the entry and add a one-line comment saying the keys must match Transport.name values.
7. src/maverick/server/ui.py:163 — the "See the docs" link points at the repository root. Point it at https://github.com/ambient-home-systems/maverick-eink-dashboard#configuration (the README and that anchor are created in the next change).
8. pyproject.toml:50 — package-data lists eink/*.css, esphome/templates/*.j2, server/static/*, server/templates/* and none of those paths exist. Reduce the list to devices/*.yaml.

Then search for anything similar you were not told about and fix it the same way, listing each in the commit message:
  grep -rn "maverick [a-z]" src/
  grep -rn "docs/" src/
  grep -rn "\-\-[a-z-]" src/maverick --include=*.py | grep -v add_argument

Acceptance:
- `python -m compileall -q src` passes and `ruff check src` reports nothing new.
- `grep -rn "render --debug\|opendisplay scan\|templates/\*.j2\|server/static\|injected automatically" src pyproject.toml` prints nothing.
- One commit, message "Fix documentation pointers that referenced missing commands and files", with one bullet per change. Open a pull request against main.
```

#### P0.2 — Verify and fix three behaviours the documentation will advertise

**Model:** Claude Opus 5 (`claude-opus-5`, alias `opus`) · **Effort:** high · **Size:** M · **Parallel:** yes · **Merged as:** [#4](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/4)  
*Why this model and effort:* Concurrency and auth reasoning with regression tests to write; this is where a wrong fix costs more than the model does.

```text
Repository: maverick-eink-dashboard (Python 3.11, src/maverick, no tests yet). Branch `fix/p0-doc-exposed-bugs` from main.

The documentation that follows will describe three behaviours the code does not deliver today. Confirm each by reading the code, fix it, and add a regression test under tests/. Create tests/ and a minimal tests/conftest.py if they do not exist; pyproject already sets testpaths = ["tests"] and asyncio_mode = "auto".

1. The MQTT last will is never registered.
   src/maverick/app.py:45 assigns `mqtt._will = (availability_topic, "offline")` after Engine.start() has already created and connected the MqttPublisher (src/maverick/engine.py:295-297). MqttPublisher.start() is the only caller of paho's will_set (src/maverick/transports/mqtt.py:56) and it returns early once connected (mqtt.py:45). Consequence: src/maverick/ha/discovery.py:21 promises that "a last-will marks them unavailable if Maverick dies", and that is false.
   Fix so the will is set before connect. The availability topic is `{mqtt.base_topic}/status` (MqttDiscovery.availability_topic). One acceptable design: Engine.start() computes the will from config and passes it to MqttPublisher(...); Application then stops writing a private attribute. Keep Engine ignorant of MqttDiscovery.
   Test: instantiate MqttPublisher with a fake paho client (monkeypatch paho.mqtt.client.Client) and assert will_set is called with the availability topic, payload "offline", qos=1, retain=True, and before connect.

2. TRMNL onboarding fails when server.api_token is set.
   GET /api/setup (src/maverick/server/api.py:246) returns `api_key` = server.api_token (api.py:260) and an `image_url` from _frame_url (api.py:317). The frame endpoint is protected by _require_token (api.py:32-49), which accepts only `Authorization: Bearer` or `?token=`. TRMNL firmware fetches image_url with neither, so every fetch is a 401 whenever api_token is set.
   Fix with the smallest change that keeps the token useful: either (a) have _frame_url append `?token=<api_token>` for the TRMNL responses only, or (b) also accept the `Access-Token` header in _require_token. Read the TRMNL BYOS documentation (https://docs.usetrmnl.com/go/diy/byos) for the exact headers the firmware sends before choosing, and explain the choice in the commit message.
   Test: FastAPI TestClient with api_token set; GET /api/display with header ID=<mac> matching a display's transport.mac, then GET the returned image_url the way the firmware would; expect 200, and 304 with If-None-Match.

3. POST /api/render?force=true ignores force.
   api.py:174-175 accepts `force` and calls application.render_all(trigger="api") without it. Thread force through Application.render_all (src/maverick/app.py) and Engine.render_all (src/maverick/engine.py).
   Test: patch Engine.render and assert force is forwarded to every display.

Also check, and record in the commit message rather than fix unless it is trivial: `image.palette_overrides` affects quantisation (PipelineOptions.palette) but not the injected stylesheet, because build_css calls get_palette(options.scheme) with no overrides (src/maverick/eink/theme.py:109). If passing overrides through ThemeOptions is a five-line change, do it and add an assertion; otherwise leave a TODO comment at theme.py:109 so the configuration reference can describe the real behaviour.

Constraints: no new dependencies beyond the existing dev extras (pytest, pytest-asyncio, ruff); httpx is already a runtime dependency so FastAPI's TestClient works. Do not skip or weaken any check. Run `ruff check src tests` and `pytest -q` and paste the output into the pull request description.
```

### Phase 1 — The front door

A README that answers what, whether, how, and a real example config. Fixes A1 and most of A10's "where do I start" problem.

#### P1.1 — README and config.example.yaml

**Model:** Claude Opus 5 (`claude-opus-5`, alias `opus`) · **Effort:** high · **Size:** M · **Parallel:** no · **Merged as:** [#5](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/5)  
*Why this model and effort:* The most-read page in the project; it must be accurate about what exists and honest about what does not, which takes judgement across the whole codebase.

```text
Repository: maverick-eink-dashboard. Branch `docs/p1-readme` from main. Assumes P0.1 and P0.2 are merged.

Write README.md at the repository root and config.example.yaml next to it. There is no README today (pyproject.toml:9 points at one), so this is the first thing anyone reads on GitHub and on PyPI.

Read in full before writing, not the docstrings only: pyproject.toml, src/maverick/cli.py, src/maverick/config.py, src/maverick/render/browser.py, src/maverick/render/dashboard.py (the authentication docstring at the top), src/maverick/transports/base.py and the other transports, src/maverick/ha/discovery.py, src/maverick/devices/panels.yaml, docs/architecture.md.

README structure. Use these headings so other pages can link to them.
1. One paragraph: what it does. Render a Home Assistant dashboard in headless Chromium, restyle it for ink, quantise it to the panel's measured ink colours, refuse to ship blank or illegible frames, and deliver over BLE (OpenDisplay), MQTT, HTTP pull (ESPHome, Kindle, TRMNL), a webhook or a file. One sentence on what it is not yet: no Home Assistant add-on or custom integration exists; docs/architecture.md holds the roadmap.
2. "Status": version 0.1.0; the render service is implemented; nothing has been tested on physical panels yet; which transports have been exercised end to end (ask the maintainer or write "unverified"; do not guess).
3. "Requirements": Python 3.11+; Chromium via `playwright install chromium`; the aarch64 note: Playwright ships no ARM Linux build, so on a Raspberry Pi install the distro `chromium` package and set MAVERICK_CHROMIUM_PATH (src/maverick/render/browser.py:51-56); a Home Assistant long-lived access token, with the rule that home_assistant.url must be the exact origin the frontend is served from, scheme and port included, or the render silently becomes a login page (dashboard.py top docstring).
4. "Install": pip from git for now (no PyPI release yet), the `[opendisplay]` extra, the `[dev]` extra.
5. "Quick start": exactly these steps, each run by you to check it: `maverick init > config.yaml`, edit three keys, `maverick check`, `maverick render kitchen --no-deliver -o out/`, look at the PNG, then `maverick serve`. Show the output of `maverick check` and `maverick render`. If you have no Home Assistant instance to run against, build the output text from RenderOutcome.describe and cmd_check in the source and label it "illustrative".
6. "Configuration", with the anchor #configuration. Do not reproduce every key (a generated reference comes later). Show the shape: home_assistant, mqtt, server, displays[]. Explain `${VAR}` and `${VAR:-default}` substitution, duration strings ("5m", "1h"), that unknown keys are errors (extra="forbid", config.py:76), and that a display needs only `id`, `panel` and `dashboard` because everything else defaults from the panel profile. Link to config.example.yaml.
7. "Panels": `maverick panels`, the generic-mono fallback with width and height overrides, and the vendors covered (28 profiles in panels.yaml; count them, do not trust this number).
8. "Transports": one row per registered transport (mqtt, opendisplay, http_pull, file, webhook): push or pull, which devices it suits, the required options. Take required options from each transport's `self.option(..., required=True)` calls and its docstring. Do not invent options.
9. "Home Assistant": three short paragraphs. MQTT discovery gives each display a device with refresh and full-refresh buttons, a scheduled-renders switch, a screen image and status sensors (ha/discovery.py). Without MQTT, a rest_command against POST /api/displays/{id}/render (give the YAML, with the bearer header if server.api_token is set). OpenDisplay tags can be delivered through Home Assistant's own Bluetooth proxies with `mode: ha`, which needs the device registry id and a /media path both processes can see (transports/opendisplay.py:9-27).
10. "HTTP API": a table of the routes from create_app in src/maverick/server/api.py; mention /api/docs; explain the frame endpoint's ETag and 304 contract and X-Maverick-Next-Refresh in two sentences, because battery devices depend on them.
11. "Running as a service": a systemd unit example. Say plainly that there is no Dockerfile or add-on yet and link the roadmap.
12. "Troubleshooting": the five failures a first-time user hits, each with the exact error text from the code and the fix: login page or blank frame (RenderError in dashboard.py), token rejected with 401 (ha/client.py), Chromium not found (browser.py RuntimeError), MQTT connect timeout (transports/mqtt.py), unknown panel (devices/profiles.py). Link docs/troubleshooting.md for the rest and create it as a one-line stub.
13. "Development": `pip install -e .[dev]`, `ruff check`, `pytest`, and a pointer to CONTRIBUTING.md (create a one-line stub).
14. "Roadmap": three bullets into docs/architecture.md: add-on and integration; pages and control surface; dashboard strategy and live preview.
15. Licence: MIT.

config.example.yaml: a commented, complete example with two displays. One Waveshare 7.5" mono over http_pull with `every: 5m` and quiet hours; one OpenDisplay Solum 2.9" BWR tag with `mode: ha` and an `on_change` trigger. Every key must exist in src/maverick/config.py (extra="forbid" rejects anything else). Prove it: `HA_TOKEN=x python -c "from maverick.config import load_config; load_config('config.example.yaml')"`. Make `maverick init` print it: add "config.example.yaml" to [tool.setuptools.package-data] and copy the file into src/maverick/, or change cmd_init (cli.py:273-275) to read the repo-root file; keep the inline fallback.

Style: British spelling, as the code uses (colour, quantise, greyscale). Second person. Short sentences. Every command in a fenced block. Describe nothing you cannot point at in the source. No badges beyond licence and Python version.

Acceptance: `maverick init | python -c "import sys,yaml; yaml.safe_load(sys.stdin)"` succeeds; every relative link in README resolves; `pip install -e .` then `pip show maverick-eink-dashboard` shows the README as the long description. Commit and open a pull request.
```

### Phase 2 — Reference, generated from the code

Configuration, panels, transports, CLI, HTTP API and MQTT references produced by a script and checked in CI, so A2, A3, A6 and A7 cannot recur.

#### P2.1 — Documentation generator and the configuration reference

**Model:** Claude Opus 5 (`claude-opus-5`, alias `opus`) · **Effort:** high · **Size:** L · **Parallel:** no · **Merged as:** [#6](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/6)  
*Why this model and effort:* Touches every model in config.py and introduces the mechanism the rest of the references depend on.

```text
Repository: maverick-eink-dashboard. Branch `docs/p2-generated-reference`. Requires P1.1.

Build a small generator so the reference pages are derived from the code and cannot drift, then use it to produce the configuration reference.

Part 1: move field documentation into the models.
src/maverick/config.py documents fields with `#:` comments (for example config.py:201, debug_artifacts), which pydantic does not expose. For every field in every model in config.py, move the comment into `Field(..., description="...")`. Keep defaults and validators unchanged. Where a field has no comment, write a one-sentence description from what the code does with it (grep the field name across src/maverick to find the consumer); do not guess. Where a field's real behaviour differs from its name, say so in the description (example: `image.palette_overrides` affects quantisation only, not the injected CSS, unless P0.2 changed that; check src/maverick/eink/theme.py:109).

Part 2: the generator.
Create scripts/gen_docs.py using the standard library and the project's own imports; no new dependencies. It must:
- Walk Config and its nested models through model_fields and write docs/reference/configuration.md: one section per model in the order a user meets them (home_assistant, mqtt, server, the top-level keys, displays[], then theme, image, render, schedule, transport, pack, esphome). Each section is a table: key, type rendered readably (`str | None`, `"5m" | float`, enum values listed), default, description. Mark required fields. Describe validators in prose under each table: duration parsing, every/cron exclusivity, the quiet_hours format, the id slug rule, rotation values, black_level below white_level.
- For the enums (DitherMode, FitMode, FrameFormat, ColorScheme) list the values with one line each, taking the meaning from the docstrings in src/maverick/eink/.
- Emit a "Transport options" section by reading each registered transport class in src/maverick/transports. TransportConfig is extra="allow" (config.py:261), so the keys are not on a model. Add to each transport a class attribute `options_doc: ClassVar[dict[str, str]]` mapping option name to description, with required options marked, and make the generator fail if a transport's source calls self.option("x") for an x that is not in options_doc.
- Start every generated file with "Generated by scripts/gen_docs.py from src/maverick; do not edit by hand" and be idempotent.
- Support `--check`: exit 1 when the generated output differs from what is committed. Add tests/test_docs_generated.py that runs the generator in check mode, so CI fails when config.py changes without a regeneration.

Part 3: prose around the tables.
docs/reference/configuration.md needs a hand-written introduction that the generator includes from docs/reference/_configuration.intro.md: how the file is found (DEFAULT_CONFIG_PATHS in cli.py:20-25 and the -c flag); environment substitution, with the exact error text when a variable is unset (expand_env in config.py); the extra="forbid" rule; and how per-display overrides merge with panel profiles (DisplayConfig.resolved).

Acceptance: `python scripts/gen_docs.py` produces identical output on a second run; `python scripts/gen_docs.py --check` passes; `pytest -q` passes; every key used in config.example.yaml appears in the generated reference (add a test for that). Commit the generated files. British spelling.
```

#### P2.2 — Panel catalogue, transports and CLI reference pages

**Model:** Claude Sonnet 5 (`claude-sonnet-5`, alias `sonnet`) · **Effort:** medium · **Size:** M · **Parallel:** yes · **Merged as:** [#7](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/7)  
*Why this model and effort:* Extends an existing generator with three well-specified pages; the judgement was spent in P2.1.

```text
Repository: maverick-eink-dashboard. Branch `docs/p2-catalogue-cli`. Requires P2.1 (scripts/gen_docs.py exists).

Extend scripts/gen_docs.py to generate three more pages, regenerate, and commit.

1. docs/reference/panels.md from src/maverick/devices/panels.yaml via maverick.devices.all_panels(). Group by vendor as `maverick panels` does. Columns: id, name, resolution, colour scheme, dpi, partial refresh, full-refresh cadence, default transport, ESPHome model, notes. Hand-written intro in docs/reference/_panels.intro.md, included by the generator: what each column means and where it is used (dpi drives the millimetre type scale in eink/theme.py and the linter in eink/lint.py; native_rotation and default_format feed DisplayConfig.resolved in config.py); how to use generic-mono with width and height overrides; a short "add a panel" recipe listing the PanelProfile fields (devices/profiles.py) and the ColorScheme values; and the statement that every value comes from datasheets and is untested on hardware unless the entry's notes say otherwise.

2. docs/reference/transports.md from maverick.transports.available_transports(). For each transport: name, push or pull (cls.pushes), description, the options table from options_doc (added in P2.1), and a hand-written paragraph included from docs/reference/_transports/<name>.md. Write those paragraphs from the class docstrings in src/maverick/transports/*.py: which devices it suits, what "delivered" means for it (the push versus pull explanation in transports/base.py), and every DeliveryResult.failure message it can return with what to do about each.

3. docs/reference/cli.md from maverick.cli.build_parser(): every subcommand with its help, positional arguments, flags and defaults, plus the global -c, --log-level and -v flags, and the environment variables MAVERICK_DEBUG, MAVERICK_MAX_RENDERS, MAVERICK_CHROMIUM_PATH and NO_COLOR (grep src/maverick for os.environ to confirm the list). Add the exit codes from main() in cli.py (0, 1, 2, 130). Hand-written intro: the recommended iteration loop (`render --no-deliver -o`, look at the PNG, tune, `check`, `serve`).

Acceptance: `python scripts/gen_docs.py --check` passes after regeneration; `pytest -q` passes; each page opens with the "Generated by" banner; British spelling. Commit and open a pull request.
```

#### P2.3 — HTTP API and MQTT reference

**Model:** Claude Opus 5 (`claude-opus-5`, alias `opus`) · **Effort:** high · **Size:** M · **Parallel:** yes · **Merged as:** [#8](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/8)  
*Why this model and effort:* Two protocol descriptions that other people will code against; every topic, header and JSON key must be lifted from the source without omission.

```text
Repository: maverick-eink-dashboard. Branch `docs/p2-api-mqtt`. Requires P2.1.

Write two reference pages by reading the code, not by paraphrasing docstrings.

1. docs/reference/http-api.md
Source: create_app in src/maverick/server/api.py. Also export the OpenAPI document: extend scripts/gen_docs.py to build the FastAPI app around an empty Config and write docs/reference/openapi.json; link it from the page.
Document every route (thirteen today) with method, path, authentication (which routes carry the bearer dependency when server.api_token is set, and the two ways to pass it: `Authorization: Bearer` and `?token=`, plus whatever P0.2 added for TRMNL), request parameters, response shape (read the dict literals) and status codes.
Give the frame endpoint its own section called "The pull protocol", because battery devices depend on it: ETag is the quoted 16-hex checksum of the panel indices (Frame.checksum in eink/pipeline.py); If-None-Match yields 304 with no body; the X-Maverick-* headers and what a device should do with each; X-Maverick-Next-Refresh is the schedule interval, or 900 s for cron schedules (_next_refresh_seconds); Date comes from the ASGI server (see the comment in api.py) so firmware needs no SNTP; Cache-Control: no-cache. Include a minimal client flow: GET with If-None-Match, sleep for Next-Refresh, repeat.
Document the TRMNL BYOS endpoints (/api/setup, /api/display): the headers TRMNL sends and what Maverick returns.
Document the setup UI at / and /api/docs, and server.enable_ui.

2. docs/reference/mqtt.md
Sources: src/maverick/ha/discovery.py, src/maverick/app.py (handle_command, _publish_state), src/maverick/transports/mqtt.py.
Sections: topic layout (`{base_topic}/status`, `{base_topic}/display/{id}/command`, `/state`, `/preview`, `/image_url`, `/meta`, `/frame`); the discovery entities table (component, unique_id, name, entity category, what it reads or sends); the command payloads (refresh, full_refresh, schedule_on, schedule_off, and that PRESS and press are accepted); the state JSON with every key from _publish_state and its meaning; the image entity's two modes (url_topic when server.base_url is set, image_topic otherwise) and why; the MqttTransport payload (the /meta JSON keys and the /frame bytes, retained, QoS 1) for people writing their own client; availability and the last will as fixed in P0.2; and how a removed display's entities are retracted (remove_display).
Add two example automations: press the refresh button when a sensor changes; turn the scheduled-renders switch off at night.

Constraints: every key and topic copied from the source, never from memory. Where behaviour is surprising (image bytes go through the broker only when base_url is unset; a 304 skips the e-ink refresh entirely) say why in one sentence, taking the reason from the code comments. British spelling.

Acceptance: someone with only these two pages can write a working pull client and a working MQTT client without reading Python. Commit and open a pull request.
```

### Phase 3 — Guides

The four pages people search for: Home Assistant, devices, design for ink, and what to do when it fails. Fixes A4, A5, A8, A9 and absorbs B7.

#### P3.1 — Home Assistant guide

**Model:** Claude Opus 5 (`claude-opus-5`, alias `opus`) · **Effort:** high · **Size:** M · **Parallel:** yes · **Merged as:** [#9](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/9)  
*Why this model and effort:* Explains the auth rule, MQTT discovery, state triggers and the OpenDisplay path in the user's terms while staying true to the code.

```text
Repository: maverick-eink-dashboard. Branch `docs/p3-home-assistant`. Requires P1.1 and P2.3.

Write docs/guides/home-assistant.md: the page a Home Assistant user reads after the README to connect Maverick to their instance and control it from there.

Read in full: src/maverick/render/dashboard.py (the authentication and shadow-DOM docstrings, build_auth_bundle, _verify_authenticated), src/maverick/ha/client.py, src/maverick/ha/discovery.py, src/maverick/app.py, src/maverick/scheduling/scheduler.py, src/maverick/transports/opendisplay.py, src/maverick/config.py (HomeAssistantConfig, MqttConfig, ScheduleConfig), docs/reference/mqtt.md, and docs/architecture.md Decision 1 for context only. Present nothing from the architecture document as existing.

Sections:
1. The token. How to create a long-lived access token (Profile, Security). Why it must be that kind of token. The URL rule: home_assistant.url must match the origin the browser will load, scheme and port included, because the frontend reads localStorage.hassTokens and redirects to login on any mismatch (dashboard.py). What frontend_url is for (reverse proxies; config.py). What verify_ssl does. Show the two RenderError messages the user sees when it is wrong and what each means.
2. Which dashboard URL to use. Paths like /lovelace-eink/kitchen versus full URLs; any scheme is accepted, so file:// works for mock-ups (resolve_url); wait_for_selector and crop_to_selector for pages that are not Home Assistant; render.settle and wait_for_images for slow cards.
3. Making it a device: MQTT discovery. Prerequisites (the Mosquitto add-on or any broker; mqtt.enabled: true; the default host core-mosquitto). What appears in Home Assistant, linking the entity table in docs/reference/mqtt.md rather than duplicating it. The three things people do with it, each as a YAML automation: press Refresh when something changes; pause the bedroom panel at night with the switch; alert on the problem binary sensor.
4. Without MQTT: a rest_command against POST /api/displays/{id}/render, with the bearer header if server.api_token is set; and the limitation that nothing appears as an entity (the set_state docstring in ha/client.py explains why MQTT is preferred).
5. Rendering when data changes. schedule.on_change with entity ids; debounce; why attribute-only changes are ignored (on_state in scheduler.py); quiet_hours; and that manual, API and button triggers bypass quiet hours while schedule and state triggers respect them (_run in scheduler.py).
6. OpenDisplay tags through Home Assistant's Bluetooth. mode: ha requires the OpenDisplay integration; device_id is the device registry id (Settings, Devices, the device, the id in the URL); Maverick writes a PNG into media_dir under media_root and calls opendisplay.upload_image with a media-source id, so the directory must be visible to Home Assistant at the same path. Contrast with mode: ble (a local adapter, the opendisplay extra, `maverick scan`).
7. What is not there yet, as one short list linking docs/architecture.md: no add-on, no custom integration, no maverick.* actions, no pages.

Where you could not execute a step yourself, add the banner used across the docs: "> Status: written from the source; not yet verified against a live Home Assistant instance." Put it only on the sections where that is true.

Acceptance: every config key mentioned exists in docs/reference/configuration.md (grep them); every entity name matches discovery.py; every YAML example parses with PyYAML. British spelling. Commit and open a pull request.
```

#### P3.2 — Device recipes

**Model:** Claude Opus 5 (`claude-opus-5`, alias `opus`) · **Effort:** high · **Size:** L · **Parallel:** yes · **Merged as:** [#10](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/10)  
*Why this model and effort:* Six device paths, one runnable client script, and strict honesty labels about what has and has not been tested.

```text
Repository: maverick-eink-dashboard. Branch `docs/p3-recipes`. Requires P2.2 and P2.3.

Create docs/recipes/ with one page per device path and docs/recipes/README.md as an index whose first line is: "None of these recipes has been run on hardware by the project yet. Each page says what was verified and how." Then be precise about that on every page.

Pages. Read the named sources in full before writing each.
1. esphome-waveshare.md: an ESP32 and a Waveshare panel over http_pull. Sources: src/maverick/esphome/generator.py, EsphomeConfig in src/maverick/config.py, the frame endpoint in src/maverick/server/api.py. Cover `maverick esphome <display>` and GET /api/displays/{id}/esphome.yaml; the pin defaults and why they match the Waveshare driver board; the online_image buffer arithmetic and the PSRAM warning above 180 KB (_buffer_size and ram_warning in generator.py), with a table of the catalogue's Waveshare panels showing which need PSRAM; deep_sleep mode and its OTA caveat; the secrets the generated file expects; a checklist for the wrong-model failure ("garbled or half-drawn screen"). State that the generated YAML has not been compiled by the project; give `esphome compile` as the user's first step and ask them to report the result.
2. opendisplay-tags.md: BLE shelf labels. Sources: src/maverick/transports/opendisplay.py, cmd_scan in cli.py, the opendisplay-* entries in devices/panels.yaml. Both modes with a decision rule (a container without Bluetooth uses ha; a standalone host with an adapter can use ble); the output of `maverick scan` and the config it prints; encryption_key, timeout and max_attempts; full versus fast refresh and full_refresh_every; and the note that Maverick sends an already-quantised image with the library's dithering off, and why.
3. kindle-kobo.md: jailbroken e-readers over http_pull with PNG. Sources: the kindle-* and kobo-* entries in panels.yaml, transports/pull.py, api.py. The frame URL; the ETag flow as a shell client (curl with If-None-Match in a loop); drawing with fbink or eips; the file transport as an rsync alternative. Do not invent jailbreak steps; link the community project the architecture document already references (hass-lovelace-kindle-screensaver).
4. trmnl.md: TRMNL in bring-your-own-server mode. Sources: trmnl_setup, trmnl_display and _display_for_mac in api.py, the trmnl-7in5 entry in panels.yaml, and P0.2's token decision. The two endpoints; the `transport.mac` key on an http_pull display; the bmp default_format and why; a curl transcript that mimics the firmware handshake.
5. inky-mqtt.md: a Raspberry Pi driving a Pimoroni Inky from the mqtt transport. Sources: the payload layout in transports/mqtt.py, the inky-* entries in panels.yaml. Write a client of about forty lines (paho-mqtt, Pillow, the inky library) that subscribes to {base}/frame and {base}/meta, decodes the PNG payload and draws it, honouring full_refresh from meta. Put it at docs/recipes/clients/inky_client.py, runnable as `python inky_client.py --host ... --display kitchen`, and syntax-check it. Say it is untested on an Inky.
6. webhook-and-file.md, short: the file transport options (path, filename, write_preview, the atomic rename), the webhook options (url, method, headers, timeout) and the X-Maverick-* headers it sends.

Each page ends with "What to check first when it does not work", drawn from that transport's DeliveryResult.failure messages.

Acceptance: every option named exists in docs/reference/transports.md; every panel id exists in panels.yaml; scripts compile; links resolve; British spelling. Commit and open a pull request.
```

#### P3.3 — E-ink design guide

**Model:** Claude Opus 5 (`claude-opus-5`, alias `opus`) · **Effort:** xhigh · **Size:** M · **Parallel:** yes · **Merged as:** [#11](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/11), [#12](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/12)  
*Why this model and effort:* Synthesis across theme, lint, dither and palette with every number recomputed from the real functions; the page the linter sends users to has to be right.

```text
Repository: maverick-eink-dashboard. Branch `docs/p3-design-guide`. Replaces the stub docs/design-guide.md from P0.1. This is the page the linter sends users to (src/maverick/eink/lint.py:173), so it must explain every linter finding and every theme rule in physical terms, and its numbers must be the code's numbers.

Read in full and derive every figure from them: src/maverick/eink/theme.py (the module docstring, ThemeOptions, TypeScale, build_css including the zoom derivation and the scheme-specific CSS), src/maverick/eink/lint.py (LintThresholds and each check), src/maverick/eink/dither.py (the AUTO mask rationale), src/maverick/eink/palette.py (the measured ink values), src/maverick/eink/pipeline.py (stage order and why), ThemeConfig and ImageConfig in src/maverick/config.py, the dpi column of src/maverick/devices/panels.yaml, and "What a prototype proved" in docs/architecture.md.

Structure:
1. Why e-ink is different: the five points from the theme.py docstring, each with the rule Maverick enforces.
2. Legibility is millimetres: the type scale (body_mm 3.2, ratio 1.25, minimum 11 px); the zoom derivation (target px = mm × dpi / 25.4; zoom = target / the 14 px reference); a table of body, small, large and xlarge sizes in px for every dpi value that occurs in panels.yaml, computed by importing maverick.eink.theme and calling TypeScale.px, never by hand. Explain what render.zoom and theme.body_mm do and when to change which.
3. Weight, rules and corners: the min_font_weight floor of 400, strong 700, rule_mm 0.25, radius 0; the hairline threshold and what "one pixel wide" measures at each dpi (25.4 / dpi).
4. Colour by panel class: mono (hierarchy from weight and size only); spot ink (accent reserved for alerts; max_spot_coverage 0.18 and why spot inks are slow); greyscale (secondary text gets a real grey); full colour (categorical only). Show the measured ink table from palette.py and say the values are approximate and overridable through image.palette_overrides, describing what that override does and does not reach after P0.2.
5. Dithering: the AUTO rule in plain words (broad midtone fields are diffused; bimodal text is snapped to the nearest ink), when to choose none, ordered or a named kernel, and what the speckle check catches.
6. The linter, finding by finding, for empty_frame, blank_render, heavy_ink, spot_ink_overuse.*, hairlines, dither_speckle, sub_threshold_pixel and palette_underused: threshold, severity, what it looks like on the panel, what to change in the dashboard, and which config key relaxes it. LintThresholds is not exposed in config today; say so and record it as a gap.
7. Card and layout advice for Home Assistant dashboards, as advice only: safe card types; columns per resolution computed from width and the body px; things that dither into mush (gauges, sparklines, gradients, camera thumbnails); the render.wait_for_selector and crop_to_selector escape hatches.
8. A worked example: an 800×480 mono panel at 124 dpi with every number filled in.

Constraints: every number must be reproducible by running functions in src/maverick/eink; put the script that produced the tables at scripts/design_tables.py and cite it at the foot of the page. No aesthetic opinion without a mechanism behind it. British spelling. Commit and open a pull request.
```

#### P3.4 — Troubleshooting

**Model:** Claude Sonnet 5 (`claude-sonnet-5`, alias `sonnet`) · **Effort:** medium · **Size:** M · **Parallel:** yes · **Merged as:** [#13](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/13)  
*Why this model and effort:* A catalogue built by grep and reading; broad but systematic, with a coverage test to keep it honest.

```text
Repository: maverick-eink-dashboard. Branch `docs/p3-troubleshooting`. Requires P1.1 (the README's short troubleshooting list links here).

Write docs/troubleshooting.md as a catalogue of every user-facing failure message in the code, with its cause and its fix.

Method:
1. Collect the messages: grep -rn "ConfigError(\|RenderError(\|HomeAssistantError(\|RuntimeError(\|DeliveryResult.failure(\|log.warning(\|log.error(" src/maverick. Expect roughly 14 ConfigError, 6 RenderError, 7 HomeAssistantError, 3 RuntimeError and 18 DeliveryResult.failure sites, plus warnings worth listing (no token configured; on_change configured but Home Assistant not connected; MQTT unavailable; state watch disconnected; could not persist frame or state).
2. For each, read the surrounding code to find the real condition, then write: the message as the user sees it, copied verbatim with its {placeholders}; where it appears (CLI output, log, the MQTT status sensor, the setup UI card); the cause; the fix; and the config key or command involved.
3. Group by stage in the order a user meets them: loading the config; connecting to Home Assistant; rendering (login page, selector never seen, crop selector, Chromium start); the lint gate (link each code to docs/design-guide.md); delivery, per transport; MQTT and discovery; the scheduler.
4. Add the items from the Risks table in docs/architecture.md as their own sections, because they hurt most: a stale login-page frame on a battery panel and how the blank_render gate prevents it; Chromium on ARM and MAVERICK_CHROMIUM_PATH; ESP32 memory and the generator's warning; colour panels looking muddy and palette_overrides.
5. Add "Reading the state": what data_dir/state.json and data_dir/frames/ contain (DisplayState and FrameStore in engine.py), how to reset a display by deleting its entries, and the debug_artifacts output.

Acceptance: every message string from the grep appears in the page. Add tests/test_troubleshooting_coverage.py that extracts the string literals at those call sites and asserts the first thirty characters of each appear in the document, with an explicit allow-list for purely internal messages. British spelling. Commit and open a pull request.
```

### Phase 4 — The architecture document

Split the evaluation into what is built and what is planned, and make its verified claims reproducible. Fixes B1–B6.

#### P4.1 — Refresh the architecture document: built versus planned

**Model:** Claude Opus 5 (`claude-opus-5`, alias `opus`) · **Effort:** xhigh · **Size:** L · **Parallel:** yes · **Merged as:** [#14](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/14)  
*Why this model and effort:* Requires reading the whole service and deciding, sentence by sentence, what is true now; the highest-judgement task in the plan.

```text
Repository: maverick-eink-dashboard. Branch `docs/p4-architecture`. Run after the Phase 2 and Phase 3 pull requests are merged so you can link them.

docs/architecture.md was written as a feasibility evaluation before the service existed. It now contradicts the code in places: the status says nothing is built; Decision 1 says "the integration supersedes MQTT discovery, which becomes a fallback" while MQTT discovery is the only Home Assistant surface; Decision 3 shows a `pages:` example the config rejects because DisplayConfig is extra="forbid". Split it into two documents and make both true.

1. docs/architecture.md: how Maverick works today. Derive it from the code; keep the evaluation's voice (short paragraphs, reasons before mechanisms). Sections:
   - The pipeline as implemented: render, then process (fit, rotate, tone, sharpen, greyscale, quantise, lint, pack; pipeline.py:12), gate, deliver, with the file that owns each stage.
   - Rendering: the browser pool and its concurrency cap (MAVERICK_MAX_RENDERS and the 250 MB spike note in browser.py); the auth bundle installed by an init script; adopted stylesheets and the attachShadow patch; login detection; the networkidle fallback; supersampling.
   - State that survives restarts and why: the DisplayState fields; FrameStore persistence and the 404-after-restart problem it solves (the FrameStore docstring in engine.py); atomic writes.
   - Push versus pull and DeliveryResult.pending (transports/base.py); the ETag contract; the skip-unchanged logic including the "servable" guard in Engine.render.
   - Full-refresh cadence and ghosting (frames_since_full, supports_partial).
   - Scheduling: interval, cron, on_change with debounce, quiet hours, render_on_start, the misfire and coalesce settings.
   - The Home Assistant surface today: MQTT discovery and the REST endpoint; why MQTT is preferred over set_state (ha/client.py).
   - The prototype findings section, kept, with each "Verified:" line linking the test that reproduces it (P4.2).
   - Known limitations, plainly: no hardware validation; LintThresholds not configurable; no add-on.
   Put "Last reviewed against commit <sha>" at the top. Remove the claude.ai "Shareable version" link at line 3.
2. docs/roadmap.md: the three decisions (installing as an app; authoring; pages and the control surface), the risks table, the sequence table and "Still open", moved across where still accurate, with a status column per phase (not started, in progress, done) and re-based estimates; the "3–5 weeks for the render service" line is done. Mark the `pages:` YAML explicitly as proposed syntax.
3. docs/architecture.html: decide and act. Either delete it (the Markdown is canonical and GitHub renders it) or generate it from the Markdown with a script under scripts/ so it cannot drift again. Do not leave two hand-maintained copies. If you keep a styled version it must be produced by `python scripts/gen_docs.py` alongside the references.
4. Update every link to architecture.md in README.md and docs/ to point at the right one of the two.
5. References: add the three integration targets the code depends on (ESPHome online_image, the TRMNL BYOS API, py-opendisplay); keep the existing ones; either cite the Home Assistant release that made strategies registerable or drop the version number.

Acceptance: no sentence in architecture.md describes something absent from src/; no sentence in roadmap.md presents something as existing; `python scripts/gen_docs.py --check` still passes; all relative links resolve. British spelling. Commit and open a pull request.
```

#### P4.2 — Make the "verified" claims reproducible

**Model:** Claude Opus 5 (`claude-opus-5`, alias `opus`) · **Effort:** high · **Size:** M · **Parallel:** yes · **Merged as:** [#15](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/15)  
*Why this model and effort:* Four numeric tests against the imaging code; the value is in choosing assertions that fail for the right reasons.

```text
Repository: maverick-eink-dashboard. Branch `test/p4-verified-claims`. Can run alongside P4.1, which links to these tests.

"What a prototype proved" in docs/architecture.md makes four measurable claims with no test behind them. Turn each into a pytest under tests/ that fails if the behaviour regresses, using only Pillow, numpy and the project code (no browser, no Home Assistant).

1. Physical type size: "a card's own 13 px text renders at 3.03 mm @124 dpi, 2.97 mm @111 dpi and 2.99 mm @300 dpi". Using maverick.eink.theme, compute 13 px × zoom / dpi × 25.4 for those dpi values and assert within ±0.05 mm. Parse the `zoom:` value out of build_css's output rather than re-deriving it, so the test checks what ships. If the zoom computation is inline only, expose it as a small function without changing the CSS output.
2. Dithering preserves text: "eroded, blotchy headings became crisp 1-bit text; speckle ratio 0.000". Build a synthetic dashboard-like image with Pillow: white ground, black text drawn with ImageDraw at about 20 px, a grey gradient block, a solid mid-grey fill. Run process() with DitherMode.AUTO for the mono palette and assert: the text region's indices equal the plain nearest-ink result (no diffusion over glyphs); the gradient region contains both inks in a mixed pattern; the speckle_ratio metric for a text-only image is below 0.001. Also assert that DitherMode.FLOYD_STEINBERG on the same image yields a higher speckle ratio, to show the AUTO mask is doing work.
3. The blank-render gate: a frame that is at least 99.5 % one ink yields a blank_render error and lint.ok is False; a frame at 99.4 % does not. Then, through Engine with a fake renderer and a fake transport, assert that block_on_lint_error skips delivery and increments skip_count, and that force=True delivers anyway.
4. Unchanged frames are not delivered: render two identical frames; the second is skipped with reason "frame unchanged" for a push transport; for a pull transport with an empty FrameStore it is delivered anyway (the "servable" guard in Engine.render). Use one fake transport class with pushes toggled.

Add tests/conftest.py fixtures for a minimal Config with one display and a FakeTransport registered through maverick.transports.register under the name "fake". Keep each test under two seconds. Update the four "Verified:" lines in docs/architecture.md to name the test functions. `pytest -q` and `ruff check tests` must be clean. Commit and open a pull request.
```

### Phase 5 — Contributors and upkeep

Make the documentation set self-maintaining: contributor guide, an AI-facing CLAUDE.md, CI that fails on drift or broken links, an index and a changelog. Fixes A10.

#### P5.1 — Contributor docs, CLAUDE.md and documentation CI

**Model:** Claude Opus 5 (`claude-opus-5`, alias `opus`) · **Effort:** high · **Size:** M · **Parallel:** no · **Merged as:** [#16](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/16)  
*Why this model and effort:* CLAUDE.md sets the rules every later AI session follows; the invariants it lists must be the real ones.

```text
Repository: maverick-eink-dashboard. Branch `docs/p5-contributing-ci`. Requires P2.1 (the generator with --check) and at least one test file.

1. CONTRIBUTING.md: development setup (`pip install -e .[dev]`, `playwright install chromium`); how to run ruff and pytest; the documentation workflow (edit models or intro files, run `python scripts/gen_docs.py`, commit the generated output; CI fails on drift); how to add a panel profile; how to add a transport (subclass Transport, @register, options_doc, a recipe page); the hardware-untested banner convention and when to remove it (a contributor reports a successful run with the panel id and firmware version); the spelling convention (British, as in the code); and a pull request checklist.

2. CLAUDE.md at the repository root, for AI-assisted changes, under 80 lines: the one-paragraph description; the module map (eink/, devices/, render/, transports/, scheduling/, server/, ha/, esphome/ and what each owns); the invariants that must not be broken (the pipeline stage order in pipeline.py:12 and why; TransportConfig is extra="allow" but DisplayConfig is extra="forbid"; frames are hashed on indices, not screenshots; FrameStore stays persisted; a lint error blocks delivery unless force); the documentation rules (generated pages are never edited by hand; run gen_docs; British spelling; no claim without a source file; the hardware-untested banner); the commands that verify a change (`ruff check src tests`, `pytest -q`, `python scripts/gen_docs.py --check`); and a short "looks like a bug but is deliberate" list taken from code comments (no Date header on the frame endpoint; Pillow's Floyd–Steinberg ignores serpentine; attribute-only state changes are ignored).

3. .github/workflows/ci.yml on push and pull_request: a Python 3.11 and 3.12 matrix; `pip install -e .[dev]`; `ruff check`; `pytest -q`; `python scripts/gen_docs.py --check`; and a Markdown link check limited to relative links, implemented as scripts/check_links.py in about thirty lines of standard-library Python so it runs offline and needs no token. Do not install Playwright browsers in CI unless a test needs them; none should.

4. docs/README.md: an index listing every page with one line each, grouped: Start here (README, quick start); Reference (generated); Guides; Recipes; Design; Project (architecture, roadmap, contributing, changelog).

Acceptance: CI is green on the pull request; each statement in CLAUDE.md points at a file; the link checker reports no broken relative links. Commit and open a pull request.
```

#### P5.2 — Changelog, single-sourced version and review stamps

**Model:** Claude Sonnet 5 (`claude-sonnet-5`, alias `sonnet`) · **Effort:** low · **Size:** S · **Parallel:** no · **Merged as:** [#17](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/17)  
*Why this model and effort:* Housekeeping with a checklist; the last step once everything else has merged.

```text
Repository: maverick-eink-dashboard. Branch `docs/p5-changelog`. Last step; all other phases merged.

1. Add CHANGELOG.md in Keep a Changelog format with an Unreleased section summarising, from `git log --oneline` since the first commit, what exists: the imaging core, the panel catalogue, config, the renderer, the engine, the transports, the scheduler, the server, the CLI, MQTT discovery, the ESPHome generator, and the documentation set added in phases 0 to 5. One line per entry.
2. Single-source the version. VERSION in src/maverick/app.py and version in pyproject.toml are both "0.1.0" by hand today. Either read the installed version through importlib.metadata in app.py, or make pyproject's version dynamic from the module; pick one. Add a --version flag to build_parser in src/maverick/cli.py if it is missing and describe the release procedure (bump, changelog, tag) in CONTRIBUTING.md.
3. Add a "Last reviewed against commit <short sha>" line to each hand-written page (the guides, recipes, design guide and troubleshooting) using the current commit, and a note in CONTRIBUTING.md that a documentation change to a page updates that line.
4. Run `python scripts/check_links.py` and `python scripts/gen_docs.py --check`; fix anything they report.

Acceptance: CI green; `maverick --version` prints 0.1.0; CHANGELOG names every top-level module. Commit and open a pull request.
```

### Phase 6 — Install from the app store

*Merged as [#18](https://github.com/ambient-home-systems/maverick-eink-dashboard/pull/18).* Not in the original plan, and done directly in the session rather than through a prompt. The request was a Home Assistant "add this repository" button for the README. That button adds the repository to Home Assistant's app store, so it only does something when the repository contains an app (the store item Home Assistant used to call an add-on). There was none, so the button came with a minimal, working app.

**What was built**

- `repository.yaml` at the root, so the repository is an app repository.
- `app/config.yaml`: slug `maverick`, aarch64 and amd64, port 5000 with a Web UI button, `mqtt:want` so the Mosquitto broker app is picked up automatically, and `addon_config`, `media` and `share` mapped read-write for the config file, the OpenDisplay `mode: ha` path and the file transport.
- `app/Dockerfile`: `ghcr.io/home-assistant/base-debian:bookworm` with Debian's own `chromium` and fonts, the package installed into a venv from this repository at a pinned commit (`MAVERICK_REF`), and a Docker `HEALTHCHECK` against `/health`. Debian on purpose: Playwright's driver needs glibc, and the distro Chromium removes the ARM problem inside the app.
- `app/run.sh`: turns the options into the environment variables the starter `maverick.yaml` reads through `${VAR}` substitution, derives the pull `base_url` from the host address when unset, takes Mosquitto's credentials from the Supervisor, writes the starter config on first start and execs `maverick serve`.
- `app/DOCS.md`, `README.md`, `CHANGELOG.md` and `translations/en.yaml` for the store and the Documentation tab; the README's install section with the my.home-assistant.io button; every page that said "there is no add-on" corrected.
- `tests/test_app.py`: the option keys `run.sh` reads are in the schema, every variable the starter config substitutes is exported, the starter config loads through `load_config`, and the app version equals the package version. A CI job lints the manifest, runs shellcheck, builds the image and launches the bundled Chromium through `BrowserPool`.

**What was verified**

- On the merged head, CI built the image on amd64, printed `maverick 0.1.0` from inside it, loaded the panel catalogue, and launched Chromium through Playwright (`chromium ok: … Chrome/120.0.0.0 … Maverick/0.1`).
- Three CI rounds were needed: `.gitignore`'s `config.yaml` rule had swallowed the manifest; the app linter rejects `boot`, `startup` and `watchdog`; both are fixed on main.
- The first button did not work. It used the `supervisor_store` redirect, copied from Home Assistant's own example repository, and that redirect takes no parameters, so the `repository_url` was dropped and the click only opened the store. Home Assistant's frontend redirect table (`src/panels/my/ha-panel-my.ts`) is the authority: `supervisor_add_addon_repository` is the one that carries `repository_url` and pre-fills the dialog. Fixed in #19.

**Still open**

- Nothing has been installed on a real Home Assistant OS system: the Supervisor build, the aarch64 image, the Mosquitto hand-off and the derived `base_url` are read from the Supervisor's documentation and bashio's source. `app/DOCS.md` says so and asks for reports.
- No ingress (the UI is on port 5000 behind the Web UI button), no pre-built image (installing builds it on the machine), and still no custom integration.
- Home Assistant renamed add-ons to *apps*; the new pages use *app* and say the words mean the same thing, while older pages still say add-on.

## How to run these

- **Each prompt is written for a fresh Claude Code session started at the repository root.** Start the session with the model and effort from the card: `claude --model opus --effort high`, or inside a session `/model opus` then `/effort high`. Aliases: `opus` is Claude Opus 5, `sonnet` is Claude Sonnet 5. Effort values are low, medium, high, xhigh and max; the default is high. Then paste the prompt.
- **Phases are ordered; prompts marked "parallel" within a phase are not.** Run Phase 0 first and merge it; every later prompt assumes its fixes. Inside a phase, prompts marked parallel can run at the same time in separate worktrees (`claude --worktree`, or EnterWorktree in a session). P1.1, P2.1, P5.1 and P5.2 gate the prompts after them.
- **Review each pull request before the next phase.** Every prompt ends with acceptance checks you can run yourself. Read the diff for claims about hardware or Home Assistant behaviour; those are the places an AI session is most likely to overstate what it verified.
- **Two prompts reward a stronger model.** P3.3 (design guide) and P4.1 (architecture split) are synthesis across the whole codebase. If your plan includes Claude Fable 5.1, `--model fable` on those two is worth the difference; the rest do not need it.
- **Optional: pin defaults with a subagent.** A file at `.claude/agents/docs-writer.md` with frontmatter `model: opus` and `effort: high` (accepted values match the session flags) gives every documentation task the same defaults without retyping them.

## Out of scope for this plan

- The custom integration and ingress for the app are product work, not documentation; the roadmap holds them. The app itself was out of scope when this was written and was built afterwards as phase 6.
- Hardware validation. Every recipe carries an untested banner until someone reports a run; the contributor guide says how to remove it. The same applies to the app on a real Home Assistant OS system.
- A documentation site. The pages are plain Markdown under docs/ with an index; a site generator can be added later without changing them.
