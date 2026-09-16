# Contributing to Maverick

Maverick renders a Home Assistant dashboard in headless Chromium, restyles it
for ink, quantises it to a panel's palette and delivers the frame. Almost
everything here can be worked on without owning an e-ink panel — the test suite
never touches a browser, a broker or a real Home Assistant — but almost nothing
here has been checked against hardware, so a report from someone who owns a
panel is worth more than a patch written from a datasheet.

1. [Development setup](#development-setup)
2. [Running the checks](#running-the-checks)
3. [The documentation workflow](#the-documentation-workflow)
4. [Adding a panel profile](#adding-a-panel-profile)
5. [Adding a transport](#adding-a-transport)
6. [The Home Assistant app](#the-home-assistant-app)
7. [The hardware-untested banner](#the-hardware-untested-banner)
8. [Spelling](#spelling)
9. [Releasing](#releasing)
10. [Pull request checklist](#pull-request-checklist)

## Development setup

Python 3.11 or newer. Clone, then install the package in editable mode with the
`dev` extra:

```bash
git clone https://github.com/ambient-home-systems/maverick-eink-dashboard
cd maverick-eink-dashboard
pip install -e ".[dev]"
```

That gives you `pytest`, `pytest-asyncio` and `ruff` alongside the runtime
dependencies (`pyproject.toml`, `[project.optional-dependencies]`).

Chromium is a separate step. It is needed to *run* Maverick, not to run the
tests:

```bash
playwright install chromium
```

On **aarch64** — a Raspberry Pi, or Home Assistant OS on ARM — Playwright ships
no Linux ARM build and that command gives you nothing usable. Install your
distribution's Chromium and point Maverick at it with
`MAVERICK_CHROMIUM_PATH=/usr/bin/chromium`, which is read in
`src/maverick/render/browser.py`.

Two environment variables are worth knowing while working:

- `MAVERICK_DEBUG=1` makes the CLI raise instead of printing a one-line error,
  so you get a traceback (`src/maverick/cli.py`).
- `render.debug_artifacts: true` on a display writes the raw screenshot and the
  quantised frame to `<data_dir>/debug/<id>/`, which is how you find out what
  Chromium actually saw.

## Running the checks

These four are the whole gate. CI runs them on Python 3.11 and 3.12
(`.github/workflows/ci.yml`), and none of them needs Chromium:

```bash
ruff check src tests                 # lint and import order; --fix applies the safe ones
pytest -q                            # the full suite; no browser, broker or network
python scripts/gen_docs.py --check   # the generated reference matches the code
python scripts/check_links.py        # every relative Markdown link resolves to a file
```

`check_links.py` is standard library only and follows relative links only, so it
needs no install, no network and no token; an external URL that rots is not this
repository's build failing.

CI runs a bare `ruff check`, which covers `scripts/` as well; `ruff check src
tests` is the narrower form worth running while you work. Lint settings live in
`pyproject.toml` under `[tool.ruff]`: 100 columns, and the `E`, `F`, `W`, `I`,
`UP`, `B`, `C4` rule sets. Two rules are ignored on purpose and the reason is
written next to them; if you need a third, put the reason there too rather than
scattering `# noqa`.

The suite runs in a bare checkout because every external thing is stubbed —
`tests/conftest.py` has a `FakeRenderer` that hands the engine a Pillow image
and a `FakeTransport` that records frames instead of reaching a panel. Use them
rather than reaching for a real browser for anything outside `tests/browser/`.

### The browser suite

`tests/browser/` is the one place a real Chromium is worth it: two findings in
`docs/architecture.md`, "What a prototype proved" — seeding the auth bundle
before the dashboard's first script runs, and adopting the injected
stylesheet into every shadow root — only fail inside an actual browser, and a
Phase 2 setup-UI flow (the Add display form, a render followed through the
status pill) only fails inside one too. These tests carry the `browser`
pytest marker (`pyproject.toml`), which `pytest -q`'s default `addopts`
excludes, so the four checks above stay exactly as fast and as install-free as
they read above. Run them explicitly, after installing Chromium the same way
`cmd_serve` needs it (`playwright install chromium`, or `MAVERICK_CHROMIUM_PATH`
on aarch64 — see "Development setup" above):

```bash
pytest -m browser
```

`tests/browser/conftest.py` skips the whole directory, rather than failing it,
when no Chromium is reachable — the same rule `BrowserPool` uses
(`src/maverick/render/browser.py`) — so a bare checkout with no Chromium still
runs this and reports skips, not errors. CI's `browser` job
(`.github/workflows/ci.yml`) is the one place that installs Chromium
(`playwright install chromium --with-deps`) and always runs these for real.
`tests/browser/fixtures/` are static pages served from a local `http.server`
thread; no Home Assistant is involved.

## The documentation workflow

Everything under `docs/reference/` except the hand-written `_*.md` includes is
**generated**. `scripts/gen_docs.py` builds it from the pydantic models in
`src/maverick/config.py`, the panel catalogue in
`src/maverick/devices/panels.yaml`, the transport registry and FastAPI's own
description of the routes. Editing a generated page by hand is undone by the
next run, and the first line of each one says so.

The loop is:

1. Edit the source of truth — a `Field(description=...)` in `config.py`, a
   panel entry, a transport's `options_doc`, or the hand-written prose in
   `docs/reference/_configuration.intro.md`, `_panels.intro.md`, `_cli.intro.md`
   or `_transports/<name>.md`.
2. Run the generator:

   ```bash
   python scripts/gen_docs.py
   ```

3. Commit the generated output along with the change that caused it.

CI fails on drift. `python scripts/gen_docs.py --check` exits 1 when a committed
page no longer matches what the generator would write, and
`tests/test_docs_generated.py` runs the same check inside the suite. The
generator is deliberately strict in three more ways, each of which fails the
build rather than emitting something stale: every field reachable from `Config`
must carry a description, every model reachable from `Config` must be claimed by
a section, and every `self.option("x")` a transport reads must appear in that
transport's `options_doc`.

Hand-written pages — `README.md`, the guides, the recipes, `docs/architecture.md`,
`docs/design-guide.md`, `docs/troubleshooting.md` — are yours to edit directly.
`docs/README.md` indexes all of them; add new pages there.

The guides, the recipes, `docs/design-guide.md` and `docs/troubleshooting.md`
each carry a `*Last reviewed against commit \`<short sha>\`.*` line under their
title. A documentation change to one of these pages updates that line to the
short SHA of the commit making the change — it says how stale the page's
factual claims might be, not when the file last moved.

## Adding a panel profile

No code change is needed. Add an entry to `src/maverick/devices/panels.yaml`
with the fields `PanelProfile` declares in `src/maverick/devices/profiles.py`:

```yaml
  - id: vendor-4in2-bwr
    name: Vendor 4.2" black/white/red
    vendor: vendor
    width: 400
    height: 300
    color_scheme: bwr          # mono bwr bwy bwry gray4 gray8 gray16 spectra6 acep7
    dpi: 119                   # from the datasheet; the default of 124 is a guess
    native_rotation: 0         # 0, 90, 180 or 270
    default_transport: http_pull
    supports_partial: false
    full_refresh_every: 0
    notes: Say here exactly what you verified, and how.
```

`dpi` is the one people skip and should not: it converts pixel sizes into
millimetres, and millimetres are what govern legibility on ink
(`src/maverick/eink/theme.py`) and what the hairline check measures
(`src/maverick/eink/lint.py`).

Then:

```bash
python scripts/gen_docs.py    # rewrites docs/reference/panels.md
python -m pytest -q
maverick panels               # your entry should appear under its vendor
```

Values transcribed from a datasheet are fine — that is where nearly all of the
catalogue came from — but say so in `notes`. An entry someone has actually run
says that instead, with the firmware version.

## Adding a transport

A transport takes a finished frame and gets it onto a panel. The push/pull split
in `src/maverick/transports/base.py` is the thing to understand first: a **push**
transport succeeds or fails now, while a **pull** transport cannot deliver
anything — it publishes the frame, returns `DeliveryResult.awaiting_pull()`, and
the real confirmation arrives later when the device fetches.

Four steps:

1. **Subclass `Transport`** in a new module under `src/maverick/transports/`.
   Set `name` (the registry key matched against `transport.type` in config),
   `pushes`, `description`, and implement `async def deliver`. `start`, `stop`
   and `probe` are optional hooks.
2. **Decorate it with `@register`**, and import the module in
   `src/maverick/transports/__init__.py` — the import is what runs the
   decorator, so it carries a `# noqa: F401`.
3. **Fill in `options_doc`**, one entry per key the transport reads.
   `TransportConfig` is `extra="allow"` (`src/maverick/config.py`), so nothing
   validates these keys and `options_doc` is the only description of them there
   is. `scripts/gen_docs.py` checks every `self.option("x")` call against it and
   fails generation if one is missing.
4. **Write the pages.** `docs/reference/_transports/<name>.md` is required —
   generation fails without it — and it is where the prose about the transport
   goes. Add a recipe under `docs/recipes/` for the device path it enables, list
   it in `docs/recipes/README.md`, and add a row to `docs/README.md`.

Then run `python scripts/gen_docs.py` and commit the regenerated
`docs/reference/transports.md`.

## The Home Assistant app

`app/` is a Home Assistant app — the store item Home Assistant used to call an
add-on — and `repository.yaml` at the root is what makes this repository
installable from the app store. The app does not vendor the package: its
`Dockerfile` installs `maverick-eink-dashboard` from this repository at the
commit named by `MAVERICK_REF`, on a Debian base image with the distro
`chromium` package, and `run.sh` turns the app's options into the environment
variables that the starter `maverick.yaml` substitutes.

Five things keep it honest, all in `tests/test_app.py`: every option `run.sh`
reads is declared in `config.yaml`'s schema, every `${VAR}` in the starter
config is exported by `run.sh`, the starter config loads with no credential set
— the state a freshly installed app is in — the app's `version` equals the
package version, and the package at `MAVERICK_REF` accepts the starter config
this commit ships. CI (`.github/workflows/ci.yml`, job `app`) lints the
manifest, builds the image on amd64, launches Chromium inside it and loads the
starter config with the package the image installed — the one place the image
is built, since nothing else in the suite touches Docker.

The last two exist because the starter config and the package reach a user's
machine by different routes. `app/rootfs/usr/share/maverick/maverick.yaml` is
copied out of the image at whatever commit the store built; the package inside
it is installed from `MAVERICK_REF`. Every model bar `TransportConfig` forbids
unknown keys (`src/maverick/config.py:71-77`), so a ref left behind at an older
release writes keys that release rejects, `maverick serve` exits on a
validation error, and the app dies on its first start — before its web UI, and
so before **Link with Home Assistant**, can be reached. That is what 0.2.0
shipped, with `MAVERICK_REF` on a commit predating
`home_assistant.refresh_token`.

The check reads the pinned commit's own config module out of git and loads
today's starter config through it, rather than comparing version numbers: a
version comparison would fail the release commit that bumps `app/config.yaml`
before the ref is allowed to move (below), while this asks the question a
user's first start actually asks. It skips in a shallow clone, where the pinned
commit is not present; CI's `app` job then covers it from inside the image.

To change the app: `app/config.yaml` for options (add a translation in
`app/translations/en.yaml` and read the key in `run.sh`), `app/run.sh` for
start-up behaviour, `app/rootfs/usr/share/maverick/maverick.yaml` for the
starter config, and `app/DOCS.md` for what users see on the app's Documentation
tab. `app/CHANGELOG.md` is the app's own changelog tab; keep it to the app.

## The hardware-untested banner

Nothing in this project has been run on a physical panel. Every page that
describes a device path therefore opens with a banner saying so, and a **What
was verified** section saying exactly how far the claims were actually checked —
a syntax check, an HTTP transcript against a stub, a generated file read back.
`docs/recipes/esphome-waveshare.md` is the model to copy:

```markdown
> **Not run on hardware by this project.** Nothing on this page has been
> flashed to an ESP32 or drawn on a panel. What *was* checked, on this
> repository, is listed under [What was verified](#what-was-verified);
> everything about the device end is read from the source and from ESPHome's
> own documentation.
```

Write the banner on any new recipe, and on any claim about how a panel, tag,
e-reader or firmware behaves that you did not observe yourself.

**A banner comes off when someone reports a successful run.** The report has to
name the panel id from the catalogue and the firmware version, alongside the
device and anything that had to be changed to make it work — open an issue with
that and the banner can be replaced by what was observed, in the page's **What
was verified** section and in the profile's `notes`. A partial run means a
narrower banner, not no banner: say which part was exercised on hardware and
which part is still read from the source. Removing a banner without a report
attached is the one documentation change that will be rejected outright.

## Spelling

Prose is **British**, matching the code's own comments and docstrings:
*colour*, *grey*, *quantise*, *behaviour*, *serialise*, *catalogue*,
*millimetre*.

The exception is anything that is already an identifier or a user-facing key,
which keeps the spelling it shipped with and must not be "corrected":
`color_scheme`, `colors`, `ColorScheme`, `gray4`/`gray8`/`gray16`,
`quantize()`. Internal names that are not part of any interface follow the prose
— `_greyscale_palette`, `is_greyscale` — so when you add one, use the British
form.

## Releasing

The version lives in one place, `pyproject.toml`'s `[project].version`;
`maverick.app.VERSION` reads it back through `importlib.metadata` (falling
back to parsing `pyproject.toml` directly in a bare checkout), so nothing else
needs editing to match.

A release is **two commits, one either side of the tag**, not a single one. The order below
is load-bearing; the two notes after it explain why, and both describe ways a
release has actually gone wrong.

1. **Bump** `version` in `pyproject.toml`.
2. **Changelog**: move the `[Unreleased]` entries in `CHANGELOG.md` under a new
   `## [x.y.z] - YYYY-MM-DD` heading, leaving an empty `[Unreleased]` section
   above it for what comes next.
3. **App version, same commit**: set `version` in `app/config.yaml` to the same
   number and add the entry to `app/CHANGELOG.md`.
   `tests/test_app.py::test_app_version_is_the_package_version` requires the
   two version numbers to match, so they have to move together or the commit
   in between fails its own tests. Leave `MAVERICK_REF` alone here.
4. **Reinstall, then regenerate**: `pip install -e .`, then
   `python scripts/gen_docs.py`, and commit what it writes.
5. **Tag** that commit `vx.y.z` and push the tag.
6. **Point `MAVERICK_REF`** in `app/Dockerfile` at the tag, as a second commit.
   Between releases it is a full commit SHA;
   `tests/test_app.py::test_dockerfile_pins_a_ref_and_uses_the_distro_chromium`
   accepts either form.

   This is the step that makes the release real, not bookkeeping:
   `tests/test_app.py::test_pinned_ref_accepts_the_starter_config` loads the
   starter config through the pinned commit's config module, so leaving the ref
   behind is what breaks a fresh install's first start rather than merely
   mislabelling it.

`maverick --version` and the HTTP API's `/` route (`src/maverick/server/api.py`)
both report `maverick.app.VERSION`, so either is how to check the bump landed.

### Why `MAVERICK_REF` moves after the tag, not with it

CI builds the app image on every pull request — `docker build` in the
`Home Assistant app` job (`.github/workflows/ci.yml`) — and the image installs
the repository from `archive/${MAVERICK_REF}.tar.gz` (`app/Dockerfile`). So a
ref named in a commit has to already exist on the remote when that commit is
pushed. Setting `MAVERICK_REF` to `vx.y.z` in the same commit that cuts the
release fails the build with a 404 from codeload, because the tag does not
exist yet.

Splitting it also leaves a window worth closing promptly. Between step 3
landing and step 6 landing, the default branch advertises the new version
while still naming the previous commit — and the app store reads
`app/config.yaml` from the default branch, so the Supervisor will offer an
update to an image built from the *old* code. Land step 6 straight after the
tag rather than leaving it for later.

If the tag cannot be pushed at all, `MAVERICK_REF` may name the release
commit's own SHA instead: it installs the same tree, and the test above accepts
it. Repointing it at the tag afterwards is then a one-line change with no
behavioural difference.

### Why the reinstall in step 4 matters

`docs/reference/openapi.json` carries the version, because `create_app` passes
`maverick.app.VERSION` to FastAPI (`src/maverick/server/api.py`). That value
comes from `importlib.metadata`, which reports whatever was installed — so an
editable install made before the bump keeps reporting the *old* version, and
`python scripts/gen_docs.py --check` passes locally against a stale file. CI
installs fresh from `pyproject.toml` (`.github/workflows/ci.yml`), reads the
new version and fails on the difference. `pip install -e .` before regenerating
is what keeps a local check honest.

## Pull request checklist

Before you open it:

- [ ] `ruff check src tests` passes.
- [ ] `python -m pytest -q` passes.
- [ ] `python scripts/gen_docs.py --check` passes — you ran the generator and
      committed what it wrote.
- [ ] `python scripts/check_links.py` passes.
- [ ] New behaviour has a test. Behaviour the docs promise has a test
      especially: `tests/` exists mostly to stop the documentation making
      claims the code does not keep.
- [ ] New or changed config keys carry a `Field(description=...)`, and new
      transport options appear in `options_doc`.
- [ ] Every factual claim in prose points at the file it came from. If you
      cannot name the source, the claim does not go in.
- [ ] Anything about hardware you did not run carries the untested banner, and
      anything you did run names the panel id and firmware version.
- [ ] Prose is in British spelling; identifiers are left alone.
- [ ] The commit describes what changed and why, not which files moved.

If you are reporting a hardware run rather than changing code, an issue is the
right place and no checklist applies — the panel id, the firmware version, what
worked and what you had to change is the whole thing.
