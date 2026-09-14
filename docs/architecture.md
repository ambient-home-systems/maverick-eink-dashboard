# Maverick — Architecture Evaluation

> **Shareable version:** https://claude.ai/code/artifact/c81a6141-db66-42ed-88d5-714700af69da
>
> Status: **feasibility evaluation.** A prototype validated the hard parts; nothing
> has been tested against real display hardware.

Rendering a live Home Assistant dashboard onto monochrome and colour e-paper — how it
should install, be authored, and be controlled from inside Home Assistant itself.

## Verdict

Feasible. **The rendering is the easy part** — the difficulty is e-ink design
enforcement and battery-aware delivery.

Screenshotting a dashboard is a solved problem. What decides whether this is any good is
whether the output is *legible on ink*, and whether a battery panel survives more than a
week. Both are addressable, and both are where the existing projects in this space stop
short.

## What a prototype proved

A working spike rendered real dashboards across five panel types. Four findings changed
the design — each was a silent failure before it was fixed.

### Frontend auth

The Home Assistant frontend ignores an `Authorization` header; it reads a token bundle
from `localStorage.hassTokens`. A long-lived token works, but `hassUrl` must match the
origin **exactly**, scheme and port included. Any mismatch silently redirects to the
login page, which then screenshots as a blank frame.

*Verified: bundle seeded pre-navigation; login-page detection blocks delivery.*

### Shadow DOM

The frontend is web components throughout, so a stylesheet on `document.head` reaches
almost nothing. Styles must be adopted into every shadow root, and `attachShadow` patched
to catch roots created by lazily-rendered cards.

*Verified: shadows flattened, font weights floored and grey secondary text remapped
inside card internals.*

### Physical type size

The one that surprised. Home Assistant hard-codes pixel font sizes, so a `font-size` rule
never reaches cards. Scaling via root `zoom` derived from panel DPI does — and legibility
on ink is governed by millimetres, not pixels.

*Verified: a card's own 13px text renders at 3.03 mm @124 dpi, 2.97 mm @111 dpi and
2.99 mm @300 dpi.*

### Dithering

Naive error diffusion destroys dashboards. Text has more local contrast than anything
else on screen, so "dither where there's contrast" eats the glyphs. Classifying by broad
midtone fields (photographs, gradients) versus bimodal regions (text) fixes it.

*Verified: eroded, blotchy headings became crisp 1-bit text; speckle ratio 0.000.*

## The pipeline

Five stages, each reversible and independently testable. Order matters: resizing after
quantisation destroys the dither pattern, and sharpening after it does nothing at all.

| # | Stage | What it does |
|---|-------|--------------|
| 1 | Render | Headless Chromium at panel geometry, e-ink stylesheet injected |
| 2 | Quantise | Content-aware dither against measured ink values |
| 3 | Pack | Native byte layout: 1/2/4 bpp, or one plane per ink |
| 4 | Gate | Lint blocks blank or illegible frames before they ship |
| 5 | Deliver | BLE, MQTT, or HTTP pull with conditional GET |

## Decision 1 — Installing it as an app

Home Assistant has no single "app" primitive. What feels like one is a bundle of three
artifacts that install as a single experience, each living in a different layer.

| Layer | Artifact | Contents |
|-------|----------|----------|
| Frontend | dashboard resources | control card, tile feature, dashboard strategy |
| HA Core | custom integration | config flow, devices and entities, services, optional MQTT fallback |
| Supervisor | add-on | render engine, Chromium, ingress UI |

- **Add-on with ingress** embeds the UI in the sidebar with auth handled by Home
  Assistant — no exposed port, no second login. **Add-ons exist only on HA OS and
  Supervised**, so Container and Core users need the same image as plain Docker plus the
  integration pointed at its URL.
- **The integration** is distributed via HACS, provides UI setup with no YAML, one device
  per panel, and `maverick.render` / `set_page` actions — **with no MQTT broker
  required**. This supersedes the earlier MQTT-discovery proposal, which becomes a
  fallback for non-Supervised users.
- **The card and strategy ship inside the integration** and auto-register, so the card
  appears without the manual "add resource" step — the most common failure in custom-card
  installs.

**On the token problem:** the add-on still needs a long-lived token, because the
supervisor token authenticates the REST API but not the frontend. The cleanest path is
the integration's config flow asking once and pushing it to the add-on over the
Supervisor API, so nobody copies a secret between two places.

## Decision 2 — Authoring e-ink dashboards

The hardest question, and the one where the obvious answer is wrong.

**Rejected: build a bespoke dashboard designer.** Perfect WYSIWYG, and constraints
enforceable by construction. But it means rebuilding entity pickers, templating and
history graphs, and abandoning the entire custom-card ecosystem — months of work to land
somewhere less capable. The right call for a product aimed at non-HA users; the wrong one
here.

**Recommended: generate a correct starting point, then show the truth.** The real problem
is that you cannot see what 1-bit 800×480 looks like until it is on the wall. Solve the
feedback loop and Home Assistant's own editor becomes adequate — and you keep every
custom card ever written.

### Layer 1 — a dashboard strategy

The strongest HA-native answer, and it improved recently: as of **2026.5** strategies are
registerable and UI-discoverable like custom cards, with a config element. The user picks
a panel model and some areas; the strategy emits an e-ink-correct dashboard — column
count matched to resolution, safe card types only, no gauges or sparklines that dither
into mush. Then "Take control" and edit normally. This turns *"designing for e-ink is
hard"* into *"pick your areas."*

### Layer 2 — see the real output while editing

- Side-by-side in the ingress UI: browser view versus the actual quantised render.
- Linter feedback that is specific, not aesthetic — *"this label is 1.9 mm at 124 dpi,
  below the 2.5 mm threshold"*, *"42% of inked pixels are hairlines"*.
- A shipped Home Assistant theme users switch to while editing, so the native editor
  itself previews flat and monochrome. Not pixel-exact, but it removes the large
  surprises for almost no work.

### Layer 3 — a template escape hatch

Let a display's source be HTML and Jinja instead of a dashboard. For dense layouts — a
wall calendar, a status board — hand-written markup beats card layout comfortably.
Supporting both paths is the differentiator: the easy one through Home Assistant's
editor, the precise one through a template.

## Decision 3 — Controlling panels from a dashboard

A control tile is straightforward, but asking for one exposes a gap: **there is no
concept of pages.** That is worth adding on its own merits — it is how a panel shows
weather in the morning and a calendar in the evening.

**Backend first.** Each display gains an ordered `pages` list — a dashboard or template
per page, with an optional dwell time and rotation — plus `next_page`, `previous_page`,
`set_page` and `refresh` actions. Automations get this for free; the tile is then a thin
client over it.

```yaml
displays:
  - id: kitchen
    pages:
      - dashboard: /eink/overview
        dwell: 30m
      - dashboard: /eink/calendar
    rotate: true
```

**Then the surface.**

- **A tile-card feature** — the control strip under a standard tile. More idiomatic for
  current Home Assistant than a bespoke card, and far less code. Page cycling belongs
  here.
- **A full card** for the live thumbnail of what the panel is showing, refresh and
  full-refresh, a page picker, and status: last render, battery, signal, error state.
- **Fleet mode** — one compact row per display, for anyone running more than three
  panels.

## Risks worth pricing in

| Severity | Risk | Detail |
|---|---|---|
| Critical | A failed render persists for hours | An expired token screenshots a login page, and a battery panel then displays it until its next wake — potentially a full day. Delivery must be gated on an automated check: a frame that is 99.5% one colour is a failure, not content. |
| High | Refreshes dominate battery life, not renders | An e-ink refresh costs orders of magnitude more energy than the render producing it. Content checksums plus conditional GET are not an optimisation — they are the design. |
| Medium | Chromium on ARM | Playwright ships no aarch64 build, so HA OS on a Raspberry Pi needs a distro Chromium and a path override. Straightforward once known; a hard stop until then. |
| Medium | ESP32 memory ceiling | A seven-colour 800×480 frame needs ~640 KB to decode; a plain ESP32 has ~200 KB usable heap. Colour panels need PSRAM, or a Pi pushing over MQTT. |
| Medium | Colour gamut and refresh cost | Spectra 6 and ACeP panels take 20–30 s per refresh and have a small, muted gamut. Quantising against sRGB primaries looks muddy; measured ink values are required. |

## Sequence

These phases are genuinely ordered — each depends on the one before it.

| # | Phase | Estimate | Outcome |
|---|-------|----------|---------|
| 1 | Add-on, ingress and integration | 3–4 weeks | It becomes an app: sidebar entry, UI setup, devices and actions |
| 2 | Pages and control surface | 2 weeks | It becomes controllable: rotation, tile feature, card, fleet view |
| 3 | Strategy, live preview and linting | 2–3 weeks | It becomes authorable by someone who has never thought about dithering |
| 4 | Template source | 1–2 weeks | Power users get precise control for dense layouts |

**8–12 weeks** for all four, against 3–5 for the render service alone. The three
questions above roughly double the scope — an argument for sequencing them, not for
dropping them.

## Still open

- No hardware testing has happened. Every panel-side claim — refresh timing, ghosting
  cadence, measured ink values — is from documentation, not a bench.
- Whether the dashboard strategy can produce layouts good enough that users accept them
  rather than immediately taking control.
- Whether OpenDisplay's BLE-proxy path is reliable enough at range to be the default, or
  whether direct BLE needs to stay first-class.
- Memory headroom for the renderer on a Raspberry Pi 4 running Home Assistant OS
  alongside everything else.

## References

- [OpenDisplay Home Assistant integration](https://www.home-assistant.io/integrations/opendisplay/)
- [py-opendisplay](https://github.com/OpenDisplay/py-opendisplay)
- [Custom dashboard strategies](https://developers.home-assistant.io/docs/frontend/custom-ui/custom-strategy/)
- [Creating custom panels](https://developers.home-assistant.io/docs/frontend/custom-ui/creating-custom-panels/)
- [hass-lovelace-kindle-screensaver](https://github.com/sibbl/hass-lovelace-kindle-screensaver)
- [tesserae](https://github.com/dmellok/tesserae)
