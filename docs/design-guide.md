# The e-ink design guide

*Last reviewed against commit `d103e74`.*

This is the page the render linter sends you to. It explains what Maverick does
to a Home Assistant dashboard before it reaches a panel, why each rule exists,
and what to change when a lint finding says something is wrong.

**There is a shortcut.** `maverick dashboard <display id>` — or **Dashboard
starter** on a display's card in the setup UI — generates a Lovelace dashboard
already sized for that panel, using only the cards this page finds safe, with
the reasoning written into the file
(`src/maverick/lovelace/generator.py`). It applies section
[2](#2-legibility-is-millimetres), [7](#7-cards-and-layout) and
[4](#4-colour-by-panel-class) for you. Read on when you want to know why it
chose what it chose, or when you are building one by hand.

## The five rules

The setup UI shows these on one screen (*Designing for ink* in its header,
rendered from `RULES` in `src/maverick/lovelace/rules.py`), beside the starter
and beside each lint finding; `tests/test_starter_dashboard.py` keeps this
section and that screen saying the same thing. Everything below is the
mechanism behind them.

**Size text in millimetres.** Body text about 3 mm tall; nothing under 2.5 mm. Legibility depends on the size on the glass, not on pixels, and 124 dpi ink has no antialiasing to save small type.

**No thin lines, no grey text.** Bold weights, solid black, borders at least a quarter of a millimetre. A two-ink panel has no grey: hairlines break up and grey text vanishes into dots.

**Cards that are text on white.** Entities, markdown, glance, weather and to-do cards. No gauges, sparklines, photos or brightness sliders. Dark glyphs on a light ground snap to the nearest ink and stay crisp; tonal shapes turn to mush.

**Colour is for alerts only.** On a panel with a red or yellow ink, use it to say 'look here' and nothing else. The colour ink refreshes slowly and ghosts; used as decoration it leaves nothing to signal with.

**Count lines, not cards.** A panel holds a fixed number of lines of text and never scrolls. What does not fit is cut off, not moved to page two; the starter's line budget is the ceiling.

Two things to know before the numbers start.

**Every figure here comes from the code.** The tables are printed verbatim by
[`scripts/design_tables.py`](../scripts/design_tables.py), which imports the
same functions the renderer runs — `maverick.eink.theme.TypeScale`, `build_css`,
`dither.quantize`, `lint.lint_frame`, the palettes in `eink.palette` — and reads
the dpi and resolution columns of `src/maverick/devices/panels.yaml`. Every
number quoted in the prose is read off one of those tables. If you change a
default in `src/maverick/eink/`, re-run the script and paste the tables back;
see [Where the numbers come from](#where-the-numbers-come-from).

**The mechanism comes before the advice.** Electrophoretic ink is a physical
process with hard limits, and almost everything in this guide follows from one
of them. Where you find an opinion without a mechanism behind it, treat it as a
bug in the page.

---

## 1. Why e-ink is different

Home Assistant's stock themes are built for emissive, 60 Hz, sixteen-million-colour
screens. Five of their assumptions are wrong for ink, and the module docstring of
[`src/maverick/eink/theme.py`](../src/maverick/eink/theme.py) lists them. Each
one has a rule Maverick enforces in the generated stylesheet.

### Thin type vanishes

Weights below 400 render as broken, dotted stems once quantised to one bit —
there is no grey to carry a half-covered pixel, so the thin part of a stroke
either becomes a full black pixel or nothing.

**The rule.** Every element gets a floored weight:

```css
* { font-weight: max(400, var(--mv-weight, 400)) !important; }
```

`--mv-weight` is the escape hatch: `h1`–`h4`, `.name`, `.heading`,
`.card-header`, `.title`, `b` and `strong` set it to 700, so the floor lifts
thin type without capping bold type. The floor is [`theme.min_font_weight`](reference/configuration.md#displaystheme),
the heading weight is `theme.strong_font_weight`.

### Grey text disappears

`--secondary-text-color` in the stock theme is a mid grey. On a one-bit panel it
quantises to pure black or pure white, so it either shouts as loudly as the
primary text or vanishes entirely. Neither is the hierarchy the dashboard
intended.

**The rule.** The whole text ramp is re-mapped onto inks the panel owns. On a
panel with no second grey, `--secondary-text-color` becomes the foreground
colour and hierarchy is carried by weight and size instead. On a multi-level
greyscale panel it becomes a real ink from the palette — see
[Colour by panel class](#4-colour-by-panel-class).

### Elevation is invisible

A box shadow is a soft gradient. Ink has no soft gradients, so every shadow
becomes a band of dither noise around every card — the most conspicuous thing on
the frame, drawing the eye to nothing.

**The rule.** Shadows, text shadows, filters, backdrop filters, `opacity` and
`background-image` are all forced off on every element and pseudo-element, and
elevation is replaced by an explicit rule: `ha-card`, `.card` and `.mdc-card`
get a `theme.rule_mm` border in the foreground ink.

### Physical size, not pixel size, governs legibility

A 14 px label is comfortable on a 300 dpi Kindle and unreadable on a 111 dpi
shelf label. The eye reads millimetres.

**The rule.** Type is sized in millimetres and converted with the panel's dpi.
Section 2 is about how.

### Animation is a lie

A screenshot catches a transition mid-flight: a card half-faded in, a graph
mid-draw, a toggle mid-slide.

**The rule.** `animation`, `animation-duration`, `transition` and
`scroll-behavior` are neutralised on everything. Ripples and scrollbars go too —
nobody taps a screenshot.

### How the rules reach the page

Home Assistant's frontend is web components all the way down, so a stylesheet on
`document.head` reaches almost nothing. Maverick injects the generated CSS into
the document **and** adopts it into every shadow root, patching `attachShadow`
so that roots created later by lazily-rendered cards get it too. This was one of
the four findings that changed the design in the prototype; see
[What a prototype proved](architecture.md#what-a-prototype-proved).

---

## 2. Legibility is millimetres

### The scale

`TypeScale` in `theme.py` is a modular scale expressed in millimetres:

| field | default | meaning |
|---|---|---|
| `body_mm` | 3.2 | body font size on the panel, in millimetres |
| `ratio` | 1.25 | each step up multiplies by this |
| `min_px` | 11 | floor below which a size is not worth rendering |

and one function:

```python
def px(self, dpi: int, step: int = 0) -> int:
    size = mm_to_px(self.body_mm, dpi) * (self.ratio**step)
    return max(self.min_px, round(size))
```

where `mm_to_px(mm, dpi)` is `mm * dpi / 25.4`. So the body target at 124 dpi is
`3.2 × 124 / 25.4 = 15.62 px`, rounded to 16.

### The zoom derivation

Setting `font-size` is not enough. Home Assistant hard-codes pixel sizes inside
its cards, and a `font-size` rule on `html` never reaches them — this was the
finding that surprised the prototype most. What does reach them is `zoom` on the
root element, because it scales the whole layout including sizes the CSS cascade
cannot touch.

So `build_css` works in two spaces. Every size in the stylesheet is written in
**reference px** against Home Assistant's own 14 px base (`REFERENCE_BASE_PX`),
and the root carries one `zoom` that converts reference space to physical size:

```
target_px = body_mm × dpi / 25.4        # rounded, floored at min_px
zoom      = target_px / 14 × zoom_multiplier
```

Writing final device pixels into the stylesheet instead would double-count the
zoom, which is why the numbers in the generated CSS look small.

The consequence worth internalising: **a fixed pixel size in Home Assistant's
own CSS becomes a fixed physical size on every panel.** The last column of the
table below is a card's own hard-coded 13 px text, in millimetres — it lands
between 2.92 and 3.06 mm on every panel in the catalogue, from a 111 dpi shelf
label to a 300 dpi Kindle.

### Sizes per panel

`TypeScale.px(dpi, step)` for the four steps that matter, at every dpi that
occurs in `panels.yaml`:

_`body_mm` 3.2, `ratio` 1.25, `min_px` 11; reference base 14 px._

| dpi | 3.2 mm in px | small | body | large | xlarge | zoom | body mm | HA's 13 px in mm | panels |
|---|---|---|---|---|---|---|---|---|---|
| 111 | 13.98 | 11 | **14** | 17 | 22 | 1.0000 | 3.20 | 2.97 | 4 |
| 119 | 14.99 | 12 | **15** | 19 | 23 | 1.0714 | 3.20 | 2.97 | 4 |
| 124 | 15.62 | 12 | **16** | 20 | 24 | 1.1429 | 3.28 | 3.04 | 6 |
| 128 | 16.13 | 13 | **16** | 20 | 25 | 1.1429 | 3.17 | 2.95 | 4 |
| 131 | 16.50 | 13 | **17** | 21 | 26 | 1.2143 | 3.30 | 3.06 | 1 |
| 132 | 16.63 | 13 | **17** | 21 | 26 | 1.2143 | 3.27 | 3.04 | 2 |
| 150 | 18.90 | 15 | **19** | 24 | 30 | 1.3571 | 3.22 | 2.99 | 1 |
| 167 | 21.04 | 17 | **21** | 26 | 33 | 1.5000 | 3.19 | 2.97 | 1 |
| 219 | 27.59 | 22 | **28** | 34 | 43 | 2.0000 | 3.25 | 3.02 | 1 |
| 227 | 28.60 | 23 | **29** | 36 | 45 | 2.0714 | 3.24 | 3.01 | 1 |
| 234 | 29.48 | 24 | **29** | 37 | 46 | 2.0714 | 3.15 | 2.92 | 1 |
| 300 | 37.80 | 30 | **38** | 47 | 59 | 2.7143 | 3.22 | 2.99 | 2 |

Two notes on reading it honestly.

**`min_px` only ever floors the body step.** `build_css` calls `scale.px()` once,
for step 0; every other step is `14 × ratio^step` in reference space, multiplied
by the same zoom. So the `small`, `large` and `xlarge` columns above are the
nominal physical scale, and the stylesheet delivers `body × ratio^step`, which
differs by at most a rounding step — at 128 dpi, for instance, `px(128, -1)` is
13 px while the stylesheet's small step lands at 12.8 px. The floor binds on no
panel in the catalogue; it would start to bind on the table's `small` column at
111 dpi if `body_mm` dropped below about 3.0 mm, and on the `body` column below
about 2.4 mm.

**Rounding the body size is what makes the physical sizes wobble.** 124 dpi
rounds 15.62 up to 16, so its body text is 3.28 mm rather than 3.20; 234 dpi
rounds 29.48 down to 29 and gets 3.15 mm. The spread across the whole catalogue
is 3.15–3.30 mm.

### `theme.body_mm` or `render.zoom`?

They are different knobs and the difference matters.

[`theme.body_mm`](reference/configuration.md#displaystheme) sets how tall body
text is **on the panel**, in millimetres. Raise it when the panel is read from
across a room and lower it when it is read at arm's length. Because zoom is
derived from it, everything else — Home Assistant's hard-coded sizes, card
padding, icon sizes — scales with it, so the dashboard keeps its proportions and
simply fits less.

[`render.zoom`](reference/configuration.md#displaysrender) is a multiplier on
the derived zoom (`ThemeOptions.zoom_multiplier`). Reach for it when you want
more or less content on the panel and are willing to accept the type size that
comes with it. It also shifts Home Assistant's own layout breakpoints, which is
what changes the number of columns the frontend itself chooses to lay out — so
it is the knob that turns a two-column view into three, where `body_mm` only
makes the two columns wider.

In practice: **size the type with `body_mm` first, then use `render.zoom` to fit
the layout, then check the type size again.** If `render.zoom` is below 1, the
body text on the panel is `body_mm × render.zoom` millimetres, not `body_mm`.

With `theme.enabled: false` there is no derived zoom to multiply, so the
renderer injects `render.zoom` as a style tag of its own
(`DashboardRenderer.needs_zoom_style_tag`). Either way it is applied exactly
once, and `theme.extra_css` makes no difference to which path runs.

---

## 3. Weight, rules and corners

### Weight

`min_font_weight` is 400 and `strong_font_weight` is 700. 400 is the floor at
which stems survive one-bit output; below it the renderer is asking the panel to
represent a stroke thinner than a pixel, and quantisation resolves that as a
dotted line. There is no partial coverage on a one-bit panel to carry it.

The strong weight is applied to `h1`–`h4`, `.name`, `.heading`, `.card-header`,
`.title`, `b` and `strong`, and on a mono panel also to `.state-on` and
`[data-state="on"]` — because with no second ink, weight is one of only three
tools left for hierarchy (the others being size and rules).

Raising `min_font_weight` above 400 is a real option on a low-dpi panel: it costs
ink coverage and gains stem width. Raising it to 700 makes everything bold, which
removes the contrast between body and heading — the `hairlines` finding will
improve and the dashboard will read worse.

### Rules

`rule_mm` is 0.25 mm. Hairlines disappear below roughly 0.2 mm, so this is a
quarter-millimetre rule that survives on every panel in the catalogue. It is
computed as

```python
rule = round(max(1.0, mm_to_px(options.rule_mm, dpi) / zoom), 2)
```

— millimetres converted to pixels, then divided back into reference space, with
a floor of one reference pixel so a rule can never be asked for as a fraction of
a pixel.

Because the zoom is itself derived from `body_mm`, the reference-space rule is
almost constant across panels (1.06–1.11 px) while the physical rule stays at
0.25 mm. The millimetre is what is being held fixed, not the pixel:

_`rule_mm` 0.25, `radius_mm` 0.0, `min_feature_mm` 0.18._

| dpi | 1 px in mm | rule (ref px) | rule (panel px) | rule in mm | tracking (em) | icon stroke (ref px) | sub_threshold_pixel |
|---|---|---|---|---|---|---|---|
| 111 | 0.2288 | 1.09 | 1.09 | 0.249 | 0.012 | 0.60 | no |
| 119 | 0.2134 | 1.09 | 1.17 | 0.249 | 0.012 | 0.60 | no |
| 124 | 0.2048 | 1.07 | 1.22 | 0.250 | 0.012 | 0.60 | no |
| 128 | 0.1984 | 1.10 | 1.26 | 0.249 | 0.012 | 0.60 | no |
| 131 | 0.1939 | 1.06 | 1.29 | 0.250 | 0.012 | 0.60 | no |
| 132 | 0.1924 | 1.07 | 1.30 | 0.250 | 0.012 | 0.60 | no |
| 150 | 0.1693 | 1.09 | 1.48 | 0.250 | 0.000 | 0.30 | yes |
| 167 | 0.1521 | 1.10 | 1.65 | 0.251 | 0.000 | 0.30 | yes |
| 219 | 0.1160 | 1.08 | 2.16 | 0.251 | 0.000 | 0.30 | yes |
| 227 | 0.1119 | 1.08 | 2.24 | 0.250 | 0.000 | 0.30 | yes |
| 234 | 0.1085 | 1.11 | 2.30 | 0.250 | 0.000 | 0.30 | yes |
| 300 | 0.0847 | 1.09 | 2.96 | 0.250 | 0.000 | 0.30 | yes |

Two other dpi-derived figures live in that table. **Tracking** is 0.012 em below
150 dpi and 0 above: adjacent stems smear together when a pixel is a fifth of a
millimetre, and opening the tracking separates them. Set
`theme.letter_spacing_em` to override it. **Icon stroke** is the width of the
hairline stroke added to MDI glyph paths — 0.6 reference px below 150 dpi, 0.3
above — because MDI icons are drawn as fills, and a stroke in `currentColor`
thickens them just enough to survive quantisation without turning them into
blobs.

### Corners

`radius_mm` is 0. A rounded corner is a curve, and a curve on a pixel grid is an
antialiased arc: a one- or two-pixel fringe of intermediate tone. That fringe is
exactly what the dither's continuous-tone mask erodes away (see
[Dithering](#5-dithering)), so the arc is snapped to the nearest ink and becomes
a visible staircase whose steps are one pixel — 0.2 mm at 124 dpi, right at the
edge of what the eye resolves at reading distance. A square corner has no
intermediate pixels at all, so there is nothing to go wrong.

If you want soft corners, the mechanism that makes them work is pixels to spare:
on a 227 or 300 dpi panel a 1 mm radius is eight to twelve pixels of arc and the
staircase disappears into the dpi.

### What "one pixel wide" means

The `hairlines` check erodes the inked mask with a 3×3 structuring element: a
pixel survives only if all eight of its neighbours are inked too. `hairline_ratio`
is then the fraction of inked pixels that did **not** survive — that is, the
fraction of the inked set lying on its own boundary.

For a long stroke `w` pixels wide, the interior is `w − 2` pixels per row, so the
ratio is exactly `2/w`, and the 0.28 threshold therefore asks for a mean stroke
around seven pixels. Bars of a known width confirm it:

_`hairline_ratio` from `lint_frame`, threshold 0.28. Bars are quantised with `dither: none` so the figure is geometry alone._

| subject | measured | 2/w | vs threshold |
|---|---|---|---|
| 1 px bars | 1.000 | 1.000 | **over** |
| 2 px bars | 1.000 | 1.000 | **over** |
| 3 px bars | 0.670 | 0.667 | **over** |
| 4 px bars | 0.505 | 0.500 | **over** |
| 6 px bars | 0.340 | 0.333 | **over** |
| 8 px bars | 0.258 | 0.250 | under |
| 12 px bars | 0.175 | 0.167 | under |
| 16 px text, regular | 1.000 | — | **over** |
| 28 px text, regular | 0.932 | — | **over** |
| 16 px text, bold | 0.864 | — | **over** |
| 28 px text, bold | 0.552 | — | **over** |

**Read that last block carefully before you act on a `hairlines` warning.** Body
text at any size a dashboard would use has stems one to three pixels wide, so a
text-dominated mono frame scores near 1.0 by construction — the check is not
telling you the dashboard is broken. It is a *relative* measure: watch it move
between renders of the same dashboard as you change weight, size or supersampling.
An 89% reading on a dense text panel is normal; the same panel jumping from 60%
to 95% after an edit is the signal.

Multiply the `2/w` row by the `1 px in mm` column to get the physical stroke
widths: 0.28 is a 7 px stroke, which is 1.4 mm at 124 dpi and 0.6 mm at 300 dpi.
A check calibrated in pixels means something different on each panel, which is
the honest reason it is a warning and never blocks delivery.

---

## 4. Colour by panel class

Every scheme, what it can pack into, and which lint checks its palette makes
possible:

_`spread` is the mean nearest-neighbour distance between inks — the threshold noise `dither._ordered` scales its Bayer matrix by, and so a direct measure of how violently ordered dithering treats a flat fill. `spot inks` are the pigments `spot_ink_overuse` counts coverage for; `accent` is the ink the theme reserves for alerts, with its luminance ratio against the panel's own white and the role that ratio earns it at a 2.0:1 floor._

| scheme | inks | bits/px | names | spread | spot inks | accent | `palette_underused` can fire |
|---|---|---|---|---|---|---|---|
| `mono` | 2 | 1 | `black`, `white` | 352 | — | — | no |
| `bwr` | 3 | 2 | `black`, `white`, `red` | 179 | `red` | `red` (3.0:1, text) | no |
| `bwy` | 3 | 2 | `black`, `white`, `yellow` | 190 | `yellow` | `yellow` (1.3:1, fill) | no |
| `bwry` | 4 | 2 | `black`, `white`, `yellow`, `red` | 145 | `yellow`, `red` | `red` (3.0:1, text) | no |
| `gray4` | 4 | 2 | `black`, `grey1`, `grey2`, `white` | 117 | — | — | no |
| `gray8` | 8 | 4 | `black`, `grey1`, `grey2`, `grey3`, `grey4`, `grey5`, `grey6`, `white` | 50 | — | — | yes |
| `gray16` | 16 | 4 | `black`, `grey1`, `grey2`, `grey3`, `grey4`, `grey5`, `grey6`, `grey7`, `grey8`, `grey9`, `grey10`, `grey11`, `grey12`, `grey13`, `grey14`, `white` | 23 | — | — | yes |
| `spectra6` | 6 | 4 | `black`, `white`, `yellow`, `red`, `blue`, `green` | 114 | `yellow`, `red`, `blue`, `green` | `red` (3.0:1, text) | yes |
| `acep7` | 7 | 4 | `black`, `white`, `green`, `blue`, `red`, `yellow`, `orange` | 91 | `green`, `blue`, `red`, `yellow`, `orange` | `red` (3.0:1, text) | yes |

`_scheme_css` in `theme.py` emits a different block for each class, in this
order: mono, then spot-ink, then greyscale, then full colour.

### Mono — hierarchy from weight and size only

One bit per pixel. There is no second text colour, so `--secondary-text-color`
is set to the foreground and the hierarchy has to come from somewhere else:

- weight — 700 on headings, and on `.state-on` / `[data-state="on"]`;
- size — `.big` and `.primary-value` get the `huge` step (step 3 of the scale,
  27.3 reference px) at 700;
- rules — the card border replaces the elevation that was carrying the grouping.

Mono also forces inline background fills back to white. The selector is a blunt
instrument and worth knowing about, because it will surprise you once:

```css
[style*="background-color: rgb(2"], [style*="background-color: rgb(1"] { … }
```

It is a literal substring match on the inline `style` attribute, so it keys off
two things and nothing else: that the declaration is written with that exact
spacing, and that the **red** channel's first digit is a 1 or a 2. It catches
what it was aimed at — a pale tinted card background such as
`rgb(240, 240, 240)` — but it also catches `rgb(20, 20, 20)`, and it misses
`rgb(90, 90, 90)` and anything written without the space after the colon. If a
card's own fill survives onto a mono panel, this is why.

### Spot ink — the accent is for alerts

`bwr`, `bwy` and `bwry` add one or two accent pigments to black and white. The
stylesheet reserves the accent for genuine alerts — nothing else on the frame
is allowed to ask for it:

```css
.alert, .warning, .error, [data-state="unavailable"], .state-unavailable {
  color: <accent> !important;     /* or a fill; see below */
}
```

and `.big` / `.primary-value` take the `xlarge` step at 700 rather than `huge`,
because the spot ink already carries the emphasis.

Two mechanisms explain why the linter polices spot coverage at
`max_spot_coverage = 0.18`:

- **A spot ink is slow.** A three-pigment panel has to drive a second particle
  population to the surface and back, and that waveform is markedly longer than
  black-and-white alone. For the multi-pigment panels the catalogue records the
  cost directly: `opendisplay-spectra-7in3` notes "*refreshes are slow (~20 s);
  schedule sparingly*" and `waveshare-5in65-acep` notes "*refresh takes ~30 s and
  is visually noisy*".
- **It is the only attention-grabbing tool the panel has.** Spend 18% of the
  frame on red and nothing on the frame is emphasised any more.

**Which ink, and whether it is the figure or the ground.** `theme._accent_ink`
takes the first of `red`, `orange`, `yellow` the palette has — blue and green
are categorical inks on a full-colour panel, not alert inks, so they are never
chosen — and then checks it against the panel's own white. At or above
`MIN_ACCENT_CONTRAST` (2.0:1) the pigment colours the text. Below it the
pigment cannot be read as a foreground at body size, so the text stays black
and the pigment becomes the fill underneath it:

```css
/* bwy: yellow is 1.3:1 as text, 6.9:1 under black */
.alert, .warning, .error, [data-state="unavailable"], .state-unavailable {
  background: rgb(206,186,70) !important;
  color: #000000 !important;
  padding: 0 0.2em;
}
```

That rule is why the `accent` column above carries a ratio and a role. It also
means a BWY panel uses its one spot pigment at all: resolving the accent by
looking up `red` alone used to leave it black, with the yellow ink never asked
for by the stylesheet.

### Greyscale — secondary text gets a real grey

`gray4`, `gray8` and `gray16` have genuine tonal hierarchy, so the stylesheet
spends it where the stock theme wanted to: `--secondary-text-color` becomes
`palette.colors[len(palette) // 3]`, a real ink from the lower third of the
ramp — `grey1` (95, 94, 92) on `gray4`, `grey2` (85, 85, 83) on `gray8`, `grey5`
(95, 94, 92) on `gray16` — and `.secondary` and `.label` use it. `.big` and
`.primary-value` take the `huge` step without a weight change, because tone is
doing the work weight would otherwise have to.

`mono` is greyscale by `ColorScheme.is_greyscale` but never reaches either of
those rules, for two independent reasons: `_scheme_css` matches the mono branch
first, and the secondary-colour remap is separately guarded by
`len(palette) > 2`, which a two-ink palette fails.

### Full colour — categorical meaning only

`spectra6` and `acep7` have a tiny gamut and a slow, visually noisy refresh.
Colour on these panels is for *categorical* meaning — this reading is a warning,
that series is the garage — never for gradients, heat maps or photographs of a
UI. The stylesheet does no more than colour `.alert`, `.warning` and `.error`
with the accent, and give `.big` / `.primary-value` the `xlarge` step at 700.

The mechanism is in the next table: seven pigments whose mean nearest-neighbour
separation is 91 units cannot represent a smooth ramp, so any gradient you ask
for is reconstructed by dithering between two of them — noisy to look at, and
paid for in refresh time.

### The measured inks

E-ink pigments are nothing like sRGB primaries. The "red" of a BWR panel is a
dull brick; Spectra 6 green is closer to olive; and a "white" background is never
255 but a light warm grey, because the particles scatter rather than emit.
Quantising against idealised primaries produces washed-out, muddy output, so
every scheme carries measured approximations of what the panel actually emits:

_Luminance uses the 0.299/0.587/0.114 weighting `dither._nearest_indices` quantises with; the ratio is against the panel's own white, not #ffffff._

| ink | measured RGB | hex | luminance | contrast vs white | schemes |
|---|---|---|---|---|---|
| `white` | `233, 231, 224` | #e9e7e0 | 231 | 1.0:1 | `mono`, `bwr`, `bwy`, `bwry`, `gray4`, `gray8`, `gray16`, `spectra6`, `acep7` |
| `black` | `26, 26, 26` | #1a1a1a | 26 | 8.9:1 | `mono`, `bwr`, `bwy`, `bwry`, `gray4`, `gray8`, `gray16`, `spectra6`, `acep7` |
| `red` | `156, 44, 40` | #9c2c28 | 77 | 3.0:1 | `bwr`, `bwry`, `spectra6`, `acep7` |
| `yellow` | `206, 186, 70` | #ceba46 | 179 | 1.3:1 | `bwy`, `bwry`, `spectra6`, `acep7` |
| `blue` | `48, 66, 129` | #304281 | 68 | 3.4:1 | `spectra6`, `acep7` |
| `green` | `62, 110, 72` | #3e6e48 | 91 | 2.5:1 | `spectra6`, `acep7` |
| `orange` | `192, 104, 46` | #c0682e | 124 | 1.9:1 | `acep7` |

The contrast column is a plain luminance ratio, not the WCAG contrast formula,
but the design consequence is the same: **yellow at 1.3:1 against the panel's
own white is not a text colour.** It is a fill or a rule. Red at 3.0:1 and blue
at 3.4:1 will carry a heading or a large readout and not 3.2 mm body text. Black
at 8.9:1 is the only ink with more than a threefold margin, which is why the
stylesheet puts every piece of running text on it.

Grey ramps are interpolated between the measured black and white rather than
between 0 and 255, so `gray16`'s lightest grey is a step down from the panel's
own white rather than a step down from a white the panel cannot make.

### Overriding them

The values are approximate and batch-dependent. Any display may replace them
with values measured from its own panel through
[`image.palette_overrides`](reference/configuration.md#displaysimage):

```yaml
displays:
  - id: kitchen
    panel: waveshare-4in2-bwr
    image:
      palette_overrides:
        red: [198, 32, 24]
```

**What the override reaches.** Both the quantisation palette
(`PipelineOptions.palette`) and the injected stylesheet
(`ThemeOptions.palette_overrides`). That pairing is the point: the page is
styled with the same inks the frame is quantised to, so the accent the CSS asks
for is the accent the quantiser will choose. Before this was wired up, a
corrected red changed the dither but not the CSS, and the two described
different panels.

**What it does not reach.** The stylesheet's foreground and background are the
literals `#000000` and `#ffffff`, not palette lookups. Overriding `black` or
`white` therefore changes what the frame is quantised *to* — the ink values
written into the preview and the packed frame — but not the colours the page is
painted with. In practice this is the right behaviour: painting the page in
`rgb(233,231,224)` would only give the quantiser a value to round back to white.
The override reaches the CSS through exactly two paths: the accent ink on a
colour panel, and the secondary-text grey on a multi-level greyscale panel.

Despite living under `image:`, the key drives both. It is keyed by palette name,
and a name that is not an ink in that scheme's palette raises from
`Palette.with_overrides` rather than being silently ignored — a typo fails the
render instead of quietly leaving the catalogue value in place.

---

## 5. Dithering

### Where it happens

`pipeline.process` runs a fixed sequence, and the order is not arbitrary:

```
fit → rotate → tone → sharpen → greyscale → quantise → lint → pack
```

- **Fit before rotate** — rotating first would fit against the wrong aspect
  ratio. A 90° or 270° panel is fitted to its transposed size and rotated
  afterwards.
- **Fit before quantise** — resizing a quantised frame destroys the dither
  pattern, because a dither is a spatial arrangement of inks and resampling
  averages it back into tones the panel cannot show.
- **Tone and sharpen before quantise** — sharpening afterwards does nothing at
  all; there is nothing left between the inks to sharpen. The unsharp mask exists
  because ink bleeds slightly and the panel loses the edge, so it has to run
  while there are still intermediate values to move.
- **Lint after quantise** — the linter inspects the *quantised* frame, which is
  what the panel will actually show, not the screenshot.

### The `auto` rule, in plain words

`DitherMode.AUTO` is the default and it decides per region:

> **A broad field of intermediate tone is error-diffused. Everything else is
> snapped to the nearest ink.**

The naive alternative — "dither wherever there is local contrast" — is wrong,
because *text* has more local contrast than anything else on a dashboard.
Diffusing across a glyph erodes its stems and blotches its bowls, which is
visibly worse than simply thresholding it.

What actually distinguishes a photograph or a gradient is a broad field of
intermediate tone. Text is bimodal: near-black glyph, near-white ground, with
intermediate values confined to a one- or two-pixel antialiasing fringe. So
`_continuous_tone_mask` selects midtone pixels — luminance strictly between 26
and 229 on the 0.299/0.587/0.114 weighting — erodes by two pixels, then dilates
back by two. **A band of intermediate tone narrower than five pixels is treated
as a glyph fringe and snapped; anything wider is diffused.** Five pixels is about
1.0 mm at 124 dpi and 0.4 mm at 300 dpi.

Inside the mask, `auto` uses Pillow's Floyd–Steinberg — the C implementation,
roughly a hundred times faster than the Python loop and the kernel `auto` wants
anyway. (Pillow's implementation is always left-to-right, so `image.serpentine`
has no effect under `auto`.)

Here is the rule working, on five patches quantised against the `mono` palette:

_400×240 patches quantised against the `mono` palette. Speckle is `speckle_ratio` from `lint_frame`; **bold** exceeds the 0.035 threshold. Ink is `coverage.ink` — the fraction of the patch that is not white._

| patch | midtone mask | speckle `auto` | speckle `none` | speckle `ordered` | ink `auto` | ink `none` | ink `ordered` |
|---|---|---|---|---|---|---|---|
| flat card, 16 px text | 0.000 | 0.0000 | 0.0000 | 0.1316 | 0.050 | 0.050 | 0.191 |
| gauge arc (grey ramp) | 0.036 | 0.0009 | 0.0000 | 0.1480 | 0.022 | 0.022 | 0.172 |
| sparkline with fill | 0.493 | **0.0715** | 0.0000 | 0.0760 | 0.080 | 0.015 | 0.236 |
| linear gradient | 0.790 | 0.0273 | 0.0000 | 0.0381 | 0.505 | 0.505 | 0.512 |
| camera thumbnail | 1.000 | 0.0021 | 0.0000 | 0.0001 | 0.504 | 0.545 | 0.514 |

The first row is the whole design: a card of 16 px text has a midtone mask of
0.000, so `auto` and `none` produce *identical* frames. Your temperature readout
is never dithered. The last row is the other end: a photographic thumbnail is
entirely midtone, entirely diffused, and comes out with almost no isolated noise
because at 50% coverage every inked pixel has inked neighbours.

### When to choose something else

| mode | choose it when | mechanism |
|---|---|---|
| `auto` | the default; anything with a mix of text and images | diffuses broad midtone fields, snaps everything else |
| `none` | the dashboard has no photographs at all | nearest ink per pixel, so speckle is 0.0000 by construction — but every midtone collapses to the nearest ink rather than being stippled into an apparent tone: the gauge arc's ramp comes back at 0.022 ink where `ordered` holds 0.172 |
| `ordered` | a panel with many inks and a small spread (`gray8` at 50, `gray16` at 23) | Bayer 8×8 threshold noise, no error propagation, so text edges survive — but the noise is scaled by the palette's own spread |
| a named kernel | photograph-heavy frames where you want a specific kernel's character | the Python error-diffusion loop against a 32³ nearest-ink LUT |

**`ordered` on a two-ink panel is a trap**, and the table shows why. The Bayer
matrix is scaled by the palette's mean nearest-neighbour distance, which for
`mono` is 352 — so it adds roughly ±176 of threshold noise to an 8-bit image.
That is enough to push part of a white background below the black/white
midpoint: the flat card's ink coverage goes from 0.050 to 0.191 and its speckle
ratio from 0.0000 to 0.1316. On `gray16`, where the spread is 23, the same
mechanism adds ±12 and behaves itself.

The named kernels — `floyd_steinberg`, `atkinson`, `burkes`, `sierra`,
`sierra_lite`, `stucki`, `jarvis` — differ in how far and how much error they
push. Their tap tables are in `dither._KERNELS` and the differences are
arithmetic, not taste: `atkinson` propagates only six eighths of the error, so it
holds highlights open and lifts contrast; `sierra_lite` has three taps and is the
cheapest; `stucki` and `jarvis` spread over three rows and five columns, which
smooths gradients and softens edges. All of them run the row-serial Python loop,
so they are far slower than `auto` — the 32³ lookup table is what makes them
usable on an 1872×1404 panel at all. `image.serpentine` alternates their scan
direction per row, which hides directional artefacts; it does nothing for `none`,
`ordered` or `auto`.

### What the speckle check catches

`speckle_ratio` counts inked pixels with **no** inked neighbour in any of the
eight directions, as a fraction of the whole frame. That is a specific failure:
sparse noise scattered over a light ground — flat UI being error-diffused. It is
deliberately not a measure of "how much dithering is happening", which is why the
camera thumbnail scores 0.0021 while being dithered from edge to edge.

The row that trips the threshold is the sparkline: a pale grey fill under a line
is a broad midtone field by the mask's definition (0.493), so it gets diffused
into a spray of isolated pixels across an otherwise white card. `dither: none`
takes that fill to 0.015 ink and 0.0000 speckle — it also takes the fill away.

---

## 6. The linter, finding by finding

The linter inspects the quantised frame — what the panel will actually show —
and reports what a person would notice standing in front of it. Everything it
knows is in
[`src/maverick/eink/lint.py`](../src/maverick/eink/lint.py).

| code | severity | threshold | metric |
|---|---|---|---|
| [`empty_frame`](#empty_frame) | error | — | — |
| [`blank_render`](#blank_render) | error | `blank_ratio` 0.995 | `coverage.<ink>` |
| [`heavy_ink`](#heavy_ink) | warning | `max_ink_coverage` 0.62 | `coverage.ink` |
| [`spot_ink_overuse.<ink>`](#spot_ink_overuse) | warning | `max_spot_coverage` 0.18 | `coverage.<ink>` |
| [`hairlines`](#hairlines) | warning | `max_hairline_ratio` 0.28 | `hairline_ratio` |
| [`dither_speckle`](#dither_speckle) | warning | `max_speckle_ratio` 0.035 | `speckle_ratio` |
| [`sub_threshold_pixel`](#sub_threshold_pixel) | info | `min_feature_mm` 0.18 | `pixel_mm` |
| [`palette_underused`](#palette_underused) | info | > 4 inks, ≤ 2 used | `inks_used` |

Only errors gate delivery, and only because
[`block_on_lint_error`](reference/configuration.md#top-level-keys) defaults to
`true`. Warnings and info findings are printed by the CLI, logged, shown on the
display's card in the setup UI, returned by the HTTP API, and written to
`<data_dir>/debug/<id>/lint.json` when `render.debug_artifacts` is on — they
never stop a frame.

Every threshold in that table is a judgement call, and each one is overridable
per display under [`displays[].lint`](reference/configuration.md#displayslint),
using the field names in the threshold column:

```yaml
displays:
  - id: kitchen
    lint:
      max_ink_coverage: 0.75     # a dense panel, deliberately
      max_hairline_ratio: 1.0    # stop reporting what you have decided to live with
```

A value outside its range fails at load rather than at the first render. Moving
a threshold is the blunt instrument, though: each finding below also says what
to change in the dashboard or the theme so the frame passes on its merits.

### `empty_frame`

**Error.** `Frame has no pixels.`

Fires when the quantised array has zero elements, and returns immediately — no
other check runs and no metrics are recorded, so a report containing only
`empty_frame` tells you nothing else about the frame.

**On the panel:** nothing. There is no frame to deliver.

**Cause:** a display whose resolved `width` or `height` is zero. The catalogue
never does this, so it means an override in your own config.

**Fix:** set real `displays[].width` and `displays[].height`, or remove the
overrides and let the `panel` entry supply them.

**Relaxed by:** nothing. `block_on_lint_error: false` would let it past the gate,
but there is no image to send.

### `blank_render`

**Error.** `Frame is {n}% '{ink}' — the dashboard almost certainly did not render.`

Fires when any single ink covers at least `blank_ratio` (0.995) of the frame.
Note that it is the *dominant* ink, not white specifically: an all-black frame
trips it too.

**On the panel:** a blank panel, or a solid one. This is the check that earns its
keep. A frame that fails it and ships anyway sits on the panel until the next
refresh, and on a battery device that can be a day.

**Cause:** almost always that the dashboard did not render. In order of
likelihood: an expired or wrong access token; a `frontend_url` whose origin does
not match `hassUrl` exactly — scheme and port included — which silently redirects
to the login page; a card that threw; a `crop_to_selector` that matched an empty
element.

**Fix:** check the token and the URL first — see
[Connecting Maverick to Home Assistant](guides/home-assistant.md#the-url-rule).
Then turn on `render.debug_artifacts: true` and re-run `maverick render <id>`;
the pre-quantisation screenshot lands at
`<data_dir>/debug/<id>/screenshot.png` and will show you a login page if that is
what happened.

**Relaxed by:** `lint.blank_ratio` on the display, or
[`block_on_lint_error: false`](reference/configuration.md#top-level-keys)
globally, or `maverick render <id> --force` for one render. The last three are
for the case where you genuinely want an almost-blank panel.

### `heavy_ink`

**Warning.** `{n}% of the frame is inked.`

`coverage.ink` is `1 − coverage[white]` — **every** non-white ink, not black
alone. On a `gray16` panel a full-bleed photograph trips this by construction,
because almost no pixel lands exactly on the panel's white.

**On the panel:** a dense, dark frame. It takes longer to refresh, it ghosts more
— the previous image leaves a visible residue — and on a spot-ink panel it slows
the refresh further.

**Fix in the dashboard:** white space and rules instead of filled blocks. The
theme already removes card backgrounds and shadows; what is left at 62% is
usually a photograph, a camera thumbnail, a dark custom card, or a graph with a
filled area.

**Relaxed by:** `lint.max_ink_coverage`, or `image.white_level` below 255 — the level stretch clips
everything at or above it to pure white, which is the direct way to take ink out
of a frame — and `image.exposure` above 1, which lightens everything before the
stretch. `image.contrast` moves where the midtones fall and can go either way.
Note that `image.black_level` works in the opposite direction: raising it crushes
the dark end to black and *adds* ink. `image.invert` is for a panel deliberately
mounted to show a negative, and swaps the budget rather than reducing it.

### `spot_ink_overuse`

**Warning.** `{n}% of the frame uses the {ink} ink.` The code carries the ink
name: `spot_ink_overuse.red`, `spot_ink_overuse.yellow`,
`spot_ink_overuse.orange`.

Fires when any one spot ink exceeds `max_spot_coverage` (0.18). Checked for
every pigment the palette has beyond black, white and the grey ramp — so `red`
and `yellow` on a BWRY panel, and `yellow`, `red`, `blue` and `green` on a
Spectra 6 one, each counted separately against the same budget.

**On the panel:** large areas of accent pigment, and a refresh that takes
markedly longer than black and white alone.

**Fix in the dashboard:** stop using alert semantics decoratively. The stylesheet
colours `.alert`, `.warning`, `.error`, `[data-state="unavailable"]` and
`.state-unavailable` with the accent; if a fifth of the frame is red, a fifth of
the frame is claiming to be an alert.

**Relaxed by:** `lint.max_spot_coverage`, or
[`theme.use_spot_colour: false`](reference/configuration.md#displaystheme),
which drops the accent back to the foreground ink and leaves the spot pigment
unused by the stylesheet. The quantiser can still reach for it if the source
image contains a matching colour.


### `hairlines`

**Warning.** `{n}% of inked pixels are one pixel wide.`

`hairline_ratio` is the fraction of inked pixels that are not interior to a 3×3
block of ink — see [What "one pixel wide" means](#what-one-pixel-wide-means) for
what that measures and why a text-heavy mono frame reads near 1.0.

**On the panel:** stems that break up into dotted lines, rules that disappear at
an angle, icon outlines that come and go between refreshes.

**Fix in the dashboard:** there is rarely anything to fix in the dashboard. Fix
it in the theme.

**Relaxed by:** `lint.max_hairline_ratio`, or
[`theme.body_mm`](reference/configuration.md#displaystheme) up
(bigger glyphs have thicker stems), `theme.min_font_weight` up from 400,
`theme.rule_mm` up from 0.25, and
[`render.supersample`](reference/configuration.md#displaysrender) at 2 — rendering
at twice the panel resolution and downsampling gives the quantiser a better
antialiased edge to work from, which is the single most effective change on a
low-dpi panel.

### `dither_speckle`

**Warning.** `{n}% of the frame is isolated dither noise.`

`speckle_ratio` counts inked pixels with no inked neighbour, over the whole
frame, against `max_speckle_ratio` (0.035).

**On the panel:** salt-and-pepper grain across card backgrounds that ought to be
clean white, with the grain most visible where the source was a pale flat fill.

**Fix in the dashboard:** find the broad midtone field. In practice it is a
filled area under a graph, a gauge's tonal arc, or a gradient in a custom card
— the [dither probe table](#the-auto-rule-in-plain-words) names the usual
suspects.

**Relaxed by:** `lint.max_speckle_ratio`, or
[`image.dither`](reference/configuration.md#dither-modes) — `auto`
is the default and already the right answer for most dashboards; `none` removes
the possibility entirely at the cost of every midtone. Also `image.sharpen`: the
unsharp mask runs *before* quantisation and creates halos of intermediate tone
around high-contrast edges, so lowering it from 0.6 (or to 0) reduces what the
mask has to diffuse.

### `sub_threshold_pixel`

**Info.** `One pixel is {n} mm at {dpi} dpi.`

Fires when `25.4 / dpi` is below `min_feature_mm` (0.18) — that is, at 142 dpi
and above. From the catalogue: 150, 167, 219, 227, 234 and 300 dpi panels all
report it; 111 to 132 dpi panels do not.

**On the panel:** nothing is wrong. This is the linter telling you that
single-pixel detail on this panel is below what the eye resolves at reading
distance, so a design that relies on hairline detail is spending pixels nobody
will see.

**Fix in the dashboard:** nothing to fix. Take it as permission to use the extra
resolution for smooth type rather than for fine detail.

**Relaxed by:** `lint.min_feature_mm`, though there is little reason to. It is
informational and depends only on the panel's `dpi`.

### `palette_underused`

**Info.** `Only {n} of {m} available inks were used.`

Fires when the palette has more than four inks and the frame used two or fewer.
That restricts it to `gray8`, `gray16`, `spectra6` and `acep7`; `bwry` and
`gray4` have exactly four and never report it.

**On the panel:** a black-and-white render on a panel that cost more because it
is not.

**Cause:** the e-ink theme does its job. It forces text to the foreground ink,
strips fills and removes gradients, so a text-only dashboard on a `gray16` panel
can legitimately come out using two inks. On a greyscale panel the theme's own
secondary-text grey usually takes it to three as soon as the dashboard has any
`.secondary` or `.label` elements.

**Fix in the dashboard:** decide whether you want the capability used. Grey
secondary text and tonal chart fills are the things a `gray16` panel is for.

**Relaxed by:** `displays[].color_scheme` (quantise to `mono` and stop being told
about inks you are not using — it also packs at 1 bit per pixel instead of 4),
or `theme.extra_css` / `theme.css_file` to reintroduce greys deliberately, or
`theme.enabled: false` if you want to style the whole page yourself.

---

## 7. Cards and layout

Everything in this section is advice. None of it is enforced, and the mechanism
is stated for each point so you can judge whether it applies to your dashboard.

### How many columns fit

A column has to fit a label and its value on one line; below that it is not a
column, it is a list of orphans. Take 24 characters as the minimum, a mean
advance of half the body size (measured at 0.55 for DejaVu Sans and 0.48 for
Liberation Sans, both in the theme's fallback stack), 2 mm of card padding, 2 mm
gutters and a 2 mm outer margin, and the arithmetic is fixed:

_24 characters minimum per column, mean advance 0.5 × body px, 2.0 mm card padding, 2.0 mm gutters, 2.0 mm outer margin, line height 1.35. Sizes are browser viewports, so a panel with a native rotation appears transposed._

| viewport | dpi | body px | columns | column px | chars/column | body lines | panels |
|---|---|---|---|---|---|---|---|
| 212×104 | 111 | 14 | 1 | 195 | 25 | 4 | `inky-phat-bwr` |
| 250×122 | 131 | 17 | 1 | 229 | 24 | 4 | `waveshare-2in13-mono` |
| 296×128 | 111 | 14 | 1 | 279 | 37 | 5 | `opendisplay-solum-2in9-bwr`, `opendisplay-flex-2in9`, `waveshare-2in9-mono` |
| 296×152 | 128 | 16 | 1 | 276 | 31 | 6 | `opendisplay-solum-2in6-bwr (rotated 270°)` |
| 400×300 | 119 | 15 | 1 | 381 | 48 | 13 | `opendisplay-solum-4in2-bwr`, `waveshare-4in2-mono`, `waveshare-4in2-bwr`, `inky-what-bwr` |
| 600×448 | 132 | 17 | 2 | 284 | 31 | 18 | `waveshare-5in65-acep`, `inky-impression-5in7` |
| 600×800 | 167 | 21 | 2 | 280 | 24 | 27 | `kindle-basic` |
| 800×480 | 124 | 16 | 3 | 254 | 29 | 21 | `generic-mono`, `opendisplay-solum-7in5-bwr`, `opendisplay-xiao-7in5`, `waveshare-7in5-mono`, `waveshare-7in5-bwr`, `trmnl-7in5` |
| 800×480 | 128 | 16 | 3 | 253 | 29 | 21 | `opendisplay-spectra-7in3`, `waveshare-7in3-spectra`, `inky-impression-7in3` |
| 800×480 | 219 | 28 | 2 | 374 | 24 | 11 | `opendisplay-mono-4in26` |
| 960×540 | 234 | 29 | 2 | 452 | 28 | 12 | `lilygo-t5-4in7` |
| 1072×1448 | 300 | 38 | 1 | 1025 | 51 | 27 | `kindle-paperwhite-3`, `kobo-clara` |
| 1600×1200 | 150 | 19 | 6 | 253 | 24 | 45 | `waveshare-13in3-gray16` |
| 1872×1404 | 227 | 29 | 4 | 446 | 28 | 34 | `waveshare-10in3-gray16` |

Read the `body lines` column as the hard ceiling on how much you can say: a
7.5-inch 800×480 panel holds twenty-one lines of body text, total, including
headings and blank space. A 2.9-inch shelf label holds five.

Two rows deserve a second look. The **1072×1448 Kindle at 300 dpi** gets one
column, not four, because 300 dpi makes the body text 38 px and a 24-character
column 1025 px wide — the panel is physically 91 mm across and physical width is
what limits columns. And **800×480 at 219 dpi** (`opendisplay-mono-4in26`) gets
two columns where the same resolution at 124 dpi gets three: the higher dpi means
a smaller physical panel, so the same pixels hold less text.

The sizes in the table are browser viewports, because that is what the layout
sees. A panel with a `native_rotation` of 90 or 270 is rendered transposed and
rotated afterwards, so the 152×296 Solum tag lays out as 296×152.

### Cards that survive

The mechanism is the continuous-tone mask from section 5. Cards whose pixels are
**bimodal** — dark glyphs on a light ground — are snapped to the nearest ink and
come out crisp, with a speckle ratio of zero. Cards that draw **broad fields of
intermediate tone** get error-diffused.

Bimodal, so safe: entity lists, glance rows, markdown, statistics-as-numbers,
headings, tiles, buttons, and anything else that is text and icons on white. The
stylesheet already strips the fills and shadows these cards would otherwise
bring.

Broad midtone fields, so not:

- **Gauges.** A gauge's arc is a tonal ramp, and the probe in section 5 shows
  what becomes of it. The arc's midtone mask is only 0.036 — a three-pixel arc is
  too thin to survive the erosion — so it is snapped to the nearest ink instead
  of diffused, and 0.022 of the patch is left inked where a stippled version
  (`ordered`) holds 0.172. You get a fragment of arc, not a gauge.
- **Sparklines and filled history graphs.** The line is fine; the pale fill under
  it is the problem. It is the one patch in the probe that exceeds the speckle
  threshold (0.0715 against 0.035).
- **Gradients**, in a custom card or a background. Midtone mask 0.790, diffused,
  and on a mono panel a gradient carries no information after quantisation
  anyway — it is one bit deep.
- **Camera thumbnails.** These actually dither well; that is what `auto` is for.
  The cost is ink: the patch comes back 50% inked, so a thumbnail filling a third
  of the frame spends about 17 of `heavy_ink`'s 62 points on its own, and dense
  frames refresh slower and ghost more.

If you want a trend on ink, prefer a line with no fill, or a bar chart with solid
bars — both are bimodal, both snap cleanly.

### Classes the stylesheet already knows

If you are writing a markdown card, a custom card or a `card-mod` rule, these are
the hooks the generated CSS already styles. Using them is cheaper than fighting
them:

| selector | what it gets |
|---|---|
| `.card-header`, `h1` | the `large` step at `strong_font_weight` |
| `h2` | the `body` step at `strong_font_weight` |
| `.secondary`, `.state`, `small`, `.label` | the `small` step; a real grey on a greyscale panel |
| `.big`, `.primary-value` | the `huge` step at 700 on mono, `huge` at the inherited weight on greyscale, `xlarge` at 700 on a spot or colour panel |
| `.value`, `.sensor-value`, `[class*="value"]`, `[class*="temperature"]` | tabular figures, so digits do not change width between refreshes and ghost |
| `.alert`, `.warning`, `.error`, `.state-unavailable`, `[data-state="unavailable"]` | the accent ink on a panel that has one |
| `.state-on`, `[data-state="on"]` | weight 700, on mono only |
| `code`, `pre`, `.monospace` | the monospace stack |

`theme.extra_css` is appended last and overrides everything above, and
`theme.css_file` is merged after it. With `theme.enabled: false` only
`extra_css` is applied — `build_theme_css` returns it directly and never reads
`css_file`.

### Escape hatches

Two render options exist for pages that do not cooperate, and both are documented
in full in the [Home Assistant guide](guides/home-assistant.md#pages-that-are-not-home-assistant):

- [`render.wait_for_selector`](reference/configuration.md#displaysrender) — wait
  for something the page really has before capturing. Defaults to
  `home-assistant`, which is present on every Home Assistant page and on nothing
  else, so a static mock-up or a third-party page needs this set or the render
  fails.
- [`render.crop_to_selector`](reference/configuration.md#displaysrender) —
  capture one element rather than the viewport. This is how you put a single card
  on a shelf label: build the card in a normal dashboard view and crop to it. A
  selector that matches nothing fails the render rather than quietly capturing
  the whole page, because a silently wrong crop is worse than a loud failure.

---

## 8. Worked example: 800×480 mono at 124 dpi

The commonest DIY panel — a Waveshare 7.5" V2 (the catalogue calls it "the most
common DIY HA dashboard panel"), the `generic-mono` fallback, a Seeed XIAO 7.5"
or a TRMNL — with every number filled in.

```yaml
displays:
  - id: kitchen
    panel: waveshare-7in5-mono     # 800×480, mono, 124 dpi
    dashboard: lovelace/eink
```

Every default applies. Here is what they resolve to:

| quantity | value | from |
|---|---|---|
| panel | 800×480 px, 164×98 mm | 800/124 in × 25.4 |
| one pixel | 0.2048 mm | 25.4 / 124 |
| body target | 15.62 px → 16 px | 3.2 × 124 / 25.4, rounded, floored at 11 |
| root zoom | 1.1429 | 16 / 14 |
| small | 11.2 ref px → 12.8 px → 2.62 mm | 14 × 1.25^-1, × zoom |
| body | 14.0 ref px → 16.0 px → 3.28 mm | 14 × 1.25^0, × zoom |
| large | 17.5 ref px → 20.0 px → 4.10 mm | 14 × 1.25^1, × zoom |
| xlarge | 21.9 ref px → 25.0 px → 5.13 mm | 14 × 1.25^2, × zoom |
| huge | 27.3 ref px → 31.2 px → 6.39 mm | 14 × 1.25^3, × zoom |
| card rule | 1.07 ref px → 1.22 px → 0.250 mm | max(1.0, 0.25 mm in px / zoom) |
| card radius | 0.0 px | radius_mm 0.0 |
| tracking | 0.012 em | dpi 124 < 150 |
| icon stroke | 0.60 ref px | dpi 124 < 150 |
| icon size | 17.5 ref px → 4.10 mm | --mdc-icon-size is the `large` step |
| HA's own 13 px text | 3.04 mm | 13 × zoom × 25.4 / 124 |
| body line | 21.6 px → 4.42 mm | body px × line-height 1.35 |
| columns | 3 × 254 px | 29 chars each |
| body lines | 21 | usable height / line height |
| palette | 2 inks, 1 bit/px, 48000 B packed | black, white |
| checks that can fire | `blank_render`, `heavy_ink`, `hairlines`, `dither_speckle` | no spot ink; 2 inks ≤ 4; 1 px = 0.2048 mm ≥ 0.18 |

The stylesheet says the same thing in its own header:

```css
   Maverick e-ink design system
   panel: mono @ 124 dpi
   body: 3.2mm = 16px on the panel, via zoom 1.143
   Sizes below are in reference px (14px base); `zoom` on
   the root converts them to physical size. This scales Home Assistant's own
   hard-coded px sizes too, which a font-size rule alone cannot reach.
   Generated - do not edit by hand.
```

### What that means for the dashboard

**Three columns of about 29 characters, twenty-one body lines deep.** A 21-line
budget is the ceiling on everything: an entity row costs one line plus its
padding, a section heading costs a line at the `large` step and the space around
it, and one `huge` readout — 6.4 mm tall, readable across a kitchen — costs two.
Count in lines before you count in cards.

**Four of the eight lint checks cannot fire.** There is no spot ink, so no
`spot_ink_overuse`. The palette is two inks, so `palette_underused` is off. One
pixel is 0.2048 mm, above `min_feature_mm`, so no `sub_threshold_pixel`. And
`empty_frame` needs a zero-size panel. What is left is `blank_render` — the one
that matters, and the one that will catch a login page before it reaches the
panel — plus three warnings.

**Expect a `hairlines` warning, and do not chase it to zero.** At 16 px body text
on a mono panel, stems are one to two pixels wide and the ratio sits near 0.9.
`render.supersample` is already 2 by default, which is the one change that helps
most; beyond that, watch the number across renders rather than obey it.

**Budget the ink.** `heavy_ink` fires above 62% non-white. For scale, the flat
text card in the [dither probe](#the-auto-rule-in-plain-words) — six lines of
16 px text — comes out at 5% ink. If a dashboard is anywhere near 62%, something
is filling large areas: a photograph, a graph fill, or a card the theme did not
manage to flatten.

**The frame is 48000 bytes packed** at 1 bit per pixel, which is what goes over
the transport. A `bwr` panel at the same resolution packs at 2 bits per pixel,
twice the payload, for a refresh that takes appreciably longer.

---

## Where the numbers come from

Every table on this page is printed by
[`scripts/design_tables.py`](../scripts/design_tables.py):

```bash
python scripts/design_tables.py                 # every table
python scripts/design_tables.py type-scale      # one table
python scripts/design_tables.py --list          # the table names
```

| table | section | derived from |
|---|---|---|
| `type-scale` | [2](#sizes-per-panel) | `TypeScale.px`, `REFERENCE_BASE_PX`, `panels.yaml` |
| `physical` | [3](#rules) | `ThemeOptions`, `LintThresholds`, `mm_to_px`, `panels.yaml` |
| `inks` | [4](#the-measured-inks) | the `INK_*` constants in `eink/palette.py` |
| `schemes` | [4](#4-colour-by-panel-class) | `get_palette`, `Palette.bits_per_pixel`, `dither._ordered` |
| `columns` | [7](#how-many-columns-fit) | `TypeScale.px`, `panels.yaml`, `DashboardRenderer.viewport_for` |
| `dither` | [5](#the-auto-rule-in-plain-words) | `dither.quantize`, `_continuous_tone_mask`, `lint_frame` |
| `hairline` | [3](#what-one-pixel-wide-means) | `lint_frame`, `lint._erode` |
| `worked-example` | [8](#8-worked-example-800480-mono-at-124-dpi) | all of the above, plus `build_css` |

The `dither` and `hairline` tables quantise synthetic test cards rather than
computing a figure, because what a check like `dither_speckle` measures is only
legible as a number you can watch move. Their text rows need DejaVu Sans, which
is in the theme's own fallback stack and on most Linux container images; without
it those rows are skipped and the table says so. Every other table is arithmetic
over this repository alone.

A last caveat on the two probe tables: they are **synthetic patches, not real
dashboards**. They are drawn by the script to isolate one property each — a
bimodal text field, a tonal arc, a filled sparkline, a gradient, a photograph —
and they were never rendered through a browser or shown on a panel. They tell you
how the quantiser and the linter behave on each kind of content, which is what
this page needs them for. What your own dashboard scores is a question only
`maverick render <id> --no-deliver` can answer.
