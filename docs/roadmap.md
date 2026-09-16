# Maverick — Roadmap

> Last reviewed against commit `42696c0`.
>
> Every feature proposed here is unbuilt, with one exception: the page list
> under Decision 3 is built, and its syntax has moved to
> [architecture.md](architecture.md#pages). What else exists is described
> there too, and where a proposal builds on it this page says so and links
> across. The YAML, entity names and actions below are proposed syntax, not
> documentation of a working feature.

The render service works. What it is not yet is an *app*: something you install
from inside Home Assistant, author dashboards for without guessing, and control
from a card. Three decisions stand between the two, and together they are more
work than the renderer was.

## Decision 1 — Installing it as an app

Home Assistant has no single "app" primitive. What feels like one would be a
bundle of three artefacts that install as a single experience, each living in a
different layer.

| Layer | Artefact | Contents |
|-------|----------|----------|
| Frontend | dashboard resources | control card, tile feature, dashboard strategy |
| HA Core | custom integration | config flow, devices and entities, actions |
| Supervisor | add-on | render engine, Chromium, ingress UI |

- **An add-on with ingress** would embed the UI with auth handled by Home
  Assistant — no exposed port, no second login. **Add-ons exist only on HA OS
  and Supervised**, so Container and Core users would need the same image as
  plain Docker plus the integration pointed at its URL. The add-on exists —
  Home Assistant now calls it an *app*, and it lives in
  [`app/`](../app/DOCS.md) — and ingress is on (`app/config.yaml`): **Open Web
  UI** embeds the setup UI inside Home Assistant on any connection, with no
  second login. It is not a sidebar panel of its own, and port 5000 stays
  published too, for panels that pull frames and as the way in if ingress is
  unavailable. The plain-Docker image Container and Core users would need now
  exists too, as the root [`Dockerfile`](../Dockerfile) — see "Docker" in
  [README.md](../README.md#docker) — but it is only that: a general-purpose
  image, with none of ingress's auth handling.
- **An integration** would be distributed via HACS, provide UI setup with no
  YAML, one device per panel, and `maverick.render` / `set_page` actions —
  **with no MQTT broker required**. That last point is the argument for it:
  today [MQTT discovery](architecture.md#the-home-assistant-surface-today) is
  the only way a display becomes a device, and it means standing up a broker for
  people who otherwise would not need one. An integration would not replace MQTT
  discovery so much as make it optional; the broker path would stay, because it
  is what works for a service running outside the Supervisor.
- **The card and strategy would ship inside the integration** and auto-register,
  so the card appears without the manual "add resource" step — the most common
  failure in custom-card installs.

**On the token problem:** *(largely solved — see
[`src/maverick/ha/auth.py`](../src/maverick/ha/auth.py))* the supervisor token
still cannot authenticate the frontend, so an app still needs a real user
credential. But it no longer has to be copied by hand: the setup UI runs Home
Assistant's IndieAuth flow itself and writes the result back to the app's own
options through the Supervisor, which every app may do without extra
permission. An integration's config flow would still be a tidier home for this,
and would cover the case where `server.base_url` is not reachable from the
user's browser, but the copy-a-secret-between-two-places problem is gone.

## Decision 2 — Authoring e-ink dashboards

The hardest question, and the one where the obvious answer is wrong.

**Rejected: build a bespoke dashboard designer.** Perfect WYSIWYG, and
constraints enforceable by construction. But it means rebuilding entity pickers,
templating and history graphs, and abandoning the entire custom-card ecosystem —
months of work to land somewhere less capable. The right call for a product
aimed at non-HA users; the wrong one here.

**Recommended: generate a correct starting point, then show the truth.** The
real problem is that you cannot see what 1-bit 800×480 looks like until it is on
the wall. Solve the feedback loop and Home Assistant's own editor becomes
adequate — and you keep every custom card ever written.

### Layer 1 — a dashboard strategy

**Status: a future goal.** What exists today is the one-click starter — the
setup UI creates a generated dashboard in Home Assistant and points the
display at it (`POST /api/displays/{id}/dashboard/create`), with *Edit in Home
Assistant* and the five rules beside it. That covers the first dashboard; the
strategy below is what would keep it generated as the home changes.

The strongest HA-native answer, and it improved when strategies became
registerable and UI-discoverable like custom cards, with a config element. The
user would pick a panel model and some areas; the strategy would emit an
e-ink-correct dashboard — column count matched to resolution, safe card types
only, no gauges or sparklines that dither into mush. Then "Take control" and
edit normally. That turns *"designing for e-ink is hard"* into *"pick your
areas."*

### Layer 2 — see the real output while editing

- Side-by-side in the ingress UI: browser view versus the actual quantised
  render. The setup UI shows the finished frame today, but not next to the
  source, and not while you edit.
- Linter feedback that is specific, not aesthetic — *"this label is 1.9 mm at
  124 dpi, below the 2.5 mm threshold"*. The linter itself exists and reports
  per-frame findings; what is missing is surfacing them per *element* while
  editing.
- A shipped Home Assistant theme users switch to while editing, so the native
  editor itself previews flat and monochrome. Not pixel-exact, but it removes
  the large surprises for almost no work. (The stylesheet Maverick injects at
  render time is not this: it lives in the renderer, not in Home Assistant's
  theme list.)

### Layer 3 — a template escape hatch

Let a display's source be HTML and Jinja instead of a dashboard. For dense
layouts — a wall calendar, a status board — hand-written markup beats card
layout comfortably. Supporting both paths is the differentiator: the easy one
through Home Assistant's editor, the precise one through a template.

The renderer already accepts any absolute URL, `file://` included, so the
missing pieces are the templating and the plumbing rather than the capture.

## Decision 3 — Controlling panels from a dashboard

A control tile is straightforward, but asking for one exposed a gap: **there was
no concept of pages.** A display rendered the one dashboard named in its config.
That was worth adding on its own merits — it is how a panel shows weather in the
morning and a calendar in the evening.

**The backend is built.** A display now carries an ordered `pages` list with an
optional dwell time and rotation, plus `set_page`, `next_page` and
`previous_page` over HTTP and MQTT, and a page picker in the setup UI.
Automations have it already; the tile is a thin client over it. The syntax, the
rotation rules and what changes a page are documented in
[architecture.md](architecture.md#pages) — this page no longer proposes them.

**What is left is the surface.**

- **A tile-card feature** — the control strip under a standard tile. More
  idiomatic for current Home Assistant than a bespoke card, and far less code.
  Page cycling belongs here; today it is the `select.<name>_page` entity and
  whatever automation you write around it
  ([MQTT reference](reference/mqtt.md#entities)).
- **A full card** for the live thumbnail of what the panel is showing, refresh
  and full-refresh, a page picker, and status: last render, battery, signal,
  error state. The MQTT `image` entity already carries the thumbnail, so the
  card would be assembling what is published rather than inventing it.
- **Fleet mode** — one compact row per display, for anyone running more than
  three panels.

## Risks worth pricing in

| Severity | Risk | Detail |
|---|---|---|
| Critical | A failed render persists for hours | An expired token screenshots a login page, and a battery panel then displays it until its next wake — potentially a full day. Delivery must be gated on an automated check: a frame that is 99.5% one colour is a failure, not content. *Addressed: `blank_render` plus the login check, see [architecture.md](architecture.md#the-pipeline-as-implemented).* |
| High | Refreshes dominate battery life, not renders | An e-ink refresh costs orders of magnitude more energy than the render producing it. Content checksums plus conditional GET are not an optimisation — they are the design. *Addressed: checksum skip and the ETag contract.* |
| Medium | Chromium on ARM | Playwright ships no aarch64 build, so HA OS on a Raspberry Pi needs a distro Chromium and a path override. Straightforward once known; a hard stop until then. *Mitigated: `MAVERICK_CHROMIUM_PATH`, and the launch failure says so.* |
| Medium | ESP32 memory ceiling | A seven-colour 800×480 frame needs ~640 KB to decode; a plain ESP32 has ~200 KB usable heap. Colour panels need PSRAM, or a Pi pushing over MQTT. *Partly mitigated: the ESPHome generator sizes the buffer and warns.* |
| Medium | Colour gamut and refresh cost | Spectra 6 and ACeP panels take 20–30 s per refresh and have a small, muted gamut. Quantising against sRGB primaries looks muddy; measured ink values are required. *Partly addressed: measured palettes ship, but no panel has confirmed them.* |

The italic notes are code rather than plans — they are the parts of the
evaluation that became the pipeline. The risks stay on this table anyway,
because not one of them has been confirmed against hardware.

## Sequence

These phases are genuinely ordered — each depends on the one before it.
Estimates are re-based on what the repository now contains.

| # | Phase | Status | Estimate | Outcome |
|---|-------|--------|----------|---------|
| 0 | The render service | Done | *was 3–5 weeks* | Pipeline, panel catalogue, five transports, scheduler, HTTP API, CLI, MQTT discovery |
| 1 | Hardware validation | Not started | 1–2 weeks | One panel per transport path confirms the catalogue, the measured inks and the refresh behaviour |
| 2 | Add-on, ingress and integration | In progress | 1–2 weeks | UI setup is done, without the integration: the app has ingress, a display store a setup UI can write to, and a display can be added, edited, previewed and removed from that UI with no restart ([architecture.md](architecture.md#the-display-store-and-precedence-rule), [architecture.md](architecture.md#changing-a-display-while-the-service-runs)). The integration itself remains, for devices and actions that need no MQTT broker and a config flow instead of a token pasted by hand |
| 3 | Pages and rotation | **Done** | *was part of 2 weeks* | An ordered `pages` list per display with dwell times and rotation, page actions over HTTP and MQTT, a Page select in Home Assistant and a picker in the setup UI ([architecture.md](architecture.md#pages)) |
| 3b | Control surface | Not started | 1–2 weeks | The Home Assistant end of it: tile feature, card, fleet view, over the page actions phase 3 built |
| 4 | Strategy, live preview and linting | Not started | 1–2 weeks | It becomes authorable by someone who has never thought about dithering. Cheaper than first estimated: the linter, the millimetre type scale and a preview pane in the setup UI all exist |
| 5 | Template source | Not started | 1 week | Power users get precise control for dense layouts. Cheaper than first estimated: the renderer already loads any URL |

**5–9 weeks remaining** — phase 2 is down to the integration alone since the
last estimate, and phase 3's page list came off the table before that —
against the three to five the render service was estimated at and the 8–12
originally put on phases 2–5. The remaining questions still more than double
the project: an argument for sequencing them, not for dropping them.

## Still open

- No hardware testing has happened. Every panel-side claim — refresh timing,
  ghosting cadence, measured ink values — is from documentation, not a bench.
  This is why phase 1 above comes before the product work, and it is
  unaffected by how much of phase 2 has since landed.
- Whether the dashboard strategy can produce layouts good enough that users
  accept them rather than immediately taking control.
- Whether OpenDisplay's BLE-proxy path is reliable enough at range to be the
  default, or whether direct BLE needs to stay first-class.
- Memory headroom for the renderer on a Raspberry Pi 4 running Home Assistant OS
  alongside everything else. The concurrency cap exists because of this
  question; nothing has answered it.
- Whether an integration should own the config, or keep reading the display
  store the service now writes for itself
  ([architecture.md](architecture.md#the-display-store-and-precedence-rule)).
  Two sources of truth for the same displays is the failure mode to avoid.

## References

- [Custom dashboard strategies](https://developers.home-assistant.io/docs/frontend/custom-ui/custom-strategy/) — registration and the config element behind Layer 1
- [Creating custom panels](https://developers.home-assistant.io/docs/frontend/custom-ui/creating-custom-panels/) — the frontend half of Decision 1
