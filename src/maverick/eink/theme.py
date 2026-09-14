"""The e-ink design system Maverick enforces on every render.

Home Assistant's stock themes are built for emissive, 60 Hz, 16-million-colour
screens. Almost every choice they make is wrong for electrophoretic ink:

* **Thin type vanishes.** Weights below 400 render as broken dotted stems once
  quantised to one bit. Maverick floors every font weight.
* **Grey text disappears.** ``--secondary-text-color`` is a mid grey that maps
  to either pure black or pure white on a mono panel, so it either shouts or
  vanishes. Maverick re-maps the whole text ramp onto inks the panel owns.
* **Elevation is invisible.** Box shadows have no analogue in ink; they dither
  into a band of noise around every card. Maverick replaces elevation with
  explicit rules.
* **Physical size, not pixel size, governs legibility.** A 14 px label is
  comfortable on a 300 dpi Kindle and unreadable on a 111 dpi shelf label.
  Maverick sizes type in millimetres and converts using the panel's dpi.
* **Animation is a lie.** A screenshot catches transitions mid-flight, so
  everything is frozen.

The output is plain CSS. The renderer injects it into the document *and* into
every shadow root, because Home Assistant's frontend is web components all the
way down.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .palette import ColorScheme, Palette, get_palette

#: Typefaces chosen for large x-height, open apertures and sturdy stems at small
#: physical sizes. All are commonly present on Linux container images; the
#: generic families at the end guarantee a sane fallback.
EINK_FONT_STACK = (
    '"Inter", "Source Sans 3", "Noto Sans", "DejaVu Sans", '
    '"Liberation Sans", system-ui, sans-serif'
)

#: Monospace for tabular data. Tabular figures stop numbers from jittering
#: between refreshes, which on e-ink reads as a smeared ghost.
EINK_MONO_STACK = '"JetBrains Mono", "Roboto Mono", "DejaVu Sans Mono", ui-monospace, monospace'


#: The Home Assistant frontend's base font size. Everything in the frontend is
#: sized relative to this, in explicit px. We keep the document at this size and
#: carry physical scaling with `zoom` instead of rewriting every rule, which is
#: what preserves HA's own visual hierarchy.
REFERENCE_BASE_PX = 14.0


def mm_to_px(mm: float, dpi: int) -> float:
    return mm * dpi / 25.4


@dataclass
class TypeScale:
    """A modular scale expressed in millimetres, resolved to px per panel."""

    body_mm: float = 3.2
    ratio: float = 1.25
    min_px: int = 11

    def px(self, dpi: int, step: int = 0) -> int:
        size = mm_to_px(self.body_mm, dpi) * (self.ratio**step)
        return max(self.min_px, round(size))


@dataclass
class ThemeOptions:
    """Knobs a user may reasonably want; every default is the e-ink-correct one."""

    dpi: int = 124
    scheme: ColorScheme = ColorScheme.MONO
    type_scale: TypeScale = field(default_factory=TypeScale)
    font_stack: str = EINK_FONT_STACK
    mono_stack: str = EINK_MONO_STACK
    #: Minimum font weight. 400 is the floor at which stems survive 1-bit output.
    min_font_weight: int = 400
    #: Weight used for headings and numeric readouts.
    strong_font_weight: int = 700
    #: Border width in mm — hairlines disappear below roughly 0.2 mm.
    rule_mm: float = 0.25
    #: Card corner radius in mm. Sharp corners dither more cleanly.
    radius_mm: float = 0.0
    #: Let the panel's spot ink carry alerts and state highlights.
    use_spot_colour: bool = True
    #: Hide the Home Assistant toolbar/sidebar chrome.
    hide_chrome: bool = True
    #: Extra tracking helps at low dpi; 0 at high dpi.
    letter_spacing_em: float | None = None
    #: Multiplies the dpi-derived zoom. Use it to fit more or less on the panel
    #: without changing the physical type size relationship.
    zoom_multiplier: float = 1.0
    #: Appended verbatim, last, so users can override anything above.
    extra_css: str = ""


def _ink(palette: Palette, name: str, fallback: str) -> str:
    if name in palette.names:
        r, g, b = palette.colors[palette.index_of(name)]
        return f"rgb({r},{g},{b})"
    return fallback


def build_css(options: ThemeOptions) -> str:
    """Generate the stylesheet for one panel."""
    dpi = options.dpi
    scale = options.type_scale
    palette = get_palette(options.scheme)

    # Physical target for body text, then everything else is expressed in
    # *reference space* and scaled by `zoom`. Expressing sizes in final device
    # px instead would double-count the zoom.
    target_body = scale.px(dpi, 0)
    zoom = (target_body / REFERENCE_BASE_PX) * options.zoom_multiplier

    def ref(step: int) -> float:
        """A type-scale step, in reference px."""
        return REFERENCE_BASE_PX * (scale.ratio**step)

    def ref_mm(mm: float) -> float:
        """Millimetres on the panel, expressed in reference px."""
        return mm_to_px(mm, dpi) / zoom if zoom else mm_to_px(mm, dpi)

    body = round(ref(0), 1)
    small = round(ref(-1), 1)
    large = round(ref(1), 1)
    xlarge = round(ref(2), 1)
    huge = round(ref(3), 1)

    rule = round(max(1.0, ref_mm(options.rule_mm)), 2)
    radius = round(ref_mm(options.radius_mm), 2)

    if options.letter_spacing_em is None:
        # Low-dpi panels smear adjacent stems together; open the tracking up.
        tracking = 0.012 if dpi < 150 else 0.0
    else:
        tracking = options.letter_spacing_em

    fg = "#000000"
    bg = "#ffffff"
    # Secondary text must remain a *distinct ink*, not a grey that collapses.
    if options.scheme.is_greyscale and len(palette) > 2:
        mid = palette.colors[len(palette) // 3]
        secondary = f"rgb({mid[0]},{mid[1]},{mid[2]})"
    else:
        secondary = fg  # On 1-bit panels there is no second text colour. Use weight.

    accent = fg
    if options.use_spot_colour and options.scheme.is_colour:
        accent = _ink(palette, "red", fg)

    return f"""
/* ==========================================================================
   Maverick e-ink design system
   panel: {options.scheme.value} @ {dpi} dpi
   body: {scale.body_mm}mm = {target_body}px on the panel, via zoom {zoom:.3f}
   Sizes below are in reference px ({REFERENCE_BASE_PX:.0f}px base); `zoom` on
   the root converts them to physical size. This scales Home Assistant's own
   hard-coded px sizes too, which a font-size rule alone cannot reach.
   Generated - do not edit by hand.
   ========================================================================== */

/* --- 1. Home Assistant theme variables ---------------------------------- */
/* Custom properties inherit through shadow roots, which is how the HA
   frontend themes itself. Setting them here reaches every component. */
html, :root, body {{
  --primary-text-color: {fg};
  --secondary-text-color: {secondary};
  --disabled-text-color: {secondary};
  --text-primary-color: {fg};
  --primary-background-color: {bg};
  --secondary-background-color: {bg};
  --card-background-color: {bg};
  --ha-card-background: {bg};
  --sidebar-background-color: {bg};
  --app-header-background-color: {bg};
  --table-row-background-color: {bg};
  --table-row-alternative-background-color: {bg};

  --primary-color: {accent};
  --accent-color: {accent};
  --dark-primary-color: {fg};
  --light-primary-color: {fg};
  --mdc-theme-primary: {accent};
  --mdc-theme-on-primary: {bg};

  --divider-color: {fg};
  --outline-color: {fg};
  --ha-card-border-color: {fg};
  --ha-card-border-width: {rule}px;
  --ha-card-border-radius: {radius}px;
  --ha-card-box-shadow: none;
  --material-shadow-elevation-2dp: none;
  --shadow-elevation-2dp_-_box-shadow: none;

  --state-icon-color: {fg};
  --state-icon-active-color: {accent};
  --paper-item-icon-color: {fg};
  --paper-item-icon-active-color: {accent};
  --switch-checked-color: {accent};
  --label-badge-background-color: {bg};
  --label-badge-text-color: {fg};
  --label-badge-red: {accent};
  --label-badge-blue: {fg};
  --label-badge-green: {fg};
  --label-badge-yellow: {fg};
  --label-badge-grey: {fg};

  --graph-color: {fg};
  --energy-grid-consumption-color: {fg};
  --sidebar-icon-color: {fg};

  color-scheme: light;
}}

/* --- 2. Freeze -------------------------------------------------------- */
/* A screenshot must never catch a transition mid-flight. */
*, *::before, *::after {{
  animation: none !important;
  animation-duration: 0s !important;
  transition: none !important;
  scroll-behavior: auto !important;
}}

/* --- 3. Flatten ------------------------------------------------------- */
/* Shadows, blurs and translucency have no representation in ink: each one
   becomes a ring of dither noise. Replace elevation with an explicit rule. */
*, *::before, *::after {{
  box-shadow: none !important;
  text-shadow: none !important;
  filter: none !important;
  backdrop-filter: none !important;
  opacity: 1 !important;
  background-image: none !important;
}}

ha-card, .card, .mdc-card {{
  border: {rule}px solid {fg} !important;
  border-radius: {radius}px !important;
  background: {bg} !important;
}}

/* --- 4. Typography ---------------------------------------------------- */
html {{
  zoom: {zoom:.4f};
}}

html, body {{
  background: {bg} !important;
  color: {fg} !important;
  font-family: {options.font_stack} !important;
  font-size: {body}px !important;
  line-height: 1.35 !important;
  letter-spacing: {tracking:.3f}em !important;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
  margin: 0 !important;
  padding: 0 !important;
}}

* {{
  font-family: inherit !important;
  /* Light and thin weights break up into dotted stems once quantised. */
  font-weight: max({options.min_font_weight}, var(--mv-weight, {options.min_font_weight})) !important;
  letter-spacing: inherit !important;
}}

h1, h2, h3, h4, .name, .heading, .card-header, .title, b, strong {{
  --mv-weight: {options.strong_font_weight};
  font-weight: {options.strong_font_weight} !important;
}}

h1, .card-header {{ font-size: {large}px !important; }}
h2 {{ font-size: {body}px !important; }}
.secondary, .state, small, .label {{ font-size: {small}px !important; }}

/* Numeric readouts: tabular figures stop digits shifting width between
   refreshes, which on e-ink reads as a ghosted smear. */
.value, .state, .sensor-value, [class*="value"], [class*="temperature"] {{
  font-variant-numeric: tabular-nums !important;
  font-feature-settings: "tnum" 1, "lnum" 1 !important;
}}

code, pre, .monospace {{ font-family: {options.mono_stack} !important; }}

/* --- 5. Contrast ------------------------------------------------------ */
/* Anything the frontend renders as a light grey on white is below the
   panel's discrimination threshold. Force it back onto a real ink. */
a, a:visited {{ color: {fg} !important; text-decoration: none !important; }}
hr, .divider {{ border-color: {fg} !important; background: {fg} !important; }}

svg, ha-icon, ha-svg-icon, ha-state-icon {{
  color: {fg} !important;
  --mdc-icon-size: {large}px;
}}
/* MDI glyphs are drawn as fills; a hairline stroke thickens them just enough
   to survive 1-bit quantisation without turning into blobs. */
ha-icon svg path, ha-svg-icon svg path {{
  stroke: currentColor !important;
  stroke-width: {max(0.2, 0.6 if dpi < 150 else 0.3):.2f}px !important;
}}

/* --- 6. Remove interactive affordances -------------------------------- */
/* Nobody taps a screenshot. */
ha-ripple, mwc-ripple, paper-ripple, .mdc-ripple-surface::before,
.mdc-ripple-surface::after {{ display: none !important; }}
::-webkit-scrollbar {{ display: none !important; width: 0 !important; }}
* {{ scrollbar-width: none !important; cursor: default !important; }}

/* --- 7. Chrome -------------------------------------------------------- */
{_chrome_css() if options.hide_chrome else "/* chrome retained */"}

/* --- 8. Panel-specific ------------------------------------------------ */
{_scheme_css(options, palette, fg, bg, accent, huge, xlarge)}

/* --- 9. User overrides ------------------------------------------------ */
{options.extra_css}
""".strip()


def _chrome_css() -> str:
    return """
app-header, .header, ha-menu-button, ha-tabs, paper-tabs, .toolbar,
app-toolbar, ha-top-app-bar-fixed, .edit-mode, ha-sidebar,
app-drawer, .action-items, ha-button-menu {
  display: none !important;
}
#view, hui-view, .view, ha-panel-lovelace {
  padding-top: 0 !important;
  margin-top: 0 !important;
  min-height: 100vh !important;
}
""".strip()


def _scheme_css(
    options: ThemeOptions,
    palette: Palette,
    fg: str,
    bg: str,
    accent: str,
    huge: int,
    xlarge: int,
) -> str:
    """Rules that only make sense for a particular class of panel."""
    if options.scheme is ColorScheme.MONO:
        return f"""
/* One bit per pixel: there is no grey, so hierarchy must come from weight,
   size and rules. Any fill that is not white is forced to solid black. */
[style*="background-color: rgb(2"], [style*="background-color: rgb(1"] {{
  background-color: {bg} !important;
}}
.state-on, [data-state="on"] {{ font-weight: 700 !important; }}
.big, .primary-value {{ font-size: {huge}px !important; font-weight: 700 !important; }}
"""
    if options.scheme.has_spot_colour:
        return f"""
/* Spot-ink panel: reserve the accent ink for genuine alerts. Using it for
   decoration wastes the only attention-grabbing tool the panel has, and each
   spot-ink refresh is markedly slower than black/white. */
.alert, .warning, .error, [data-state="unavailable"], .state-unavailable {{
  color: {accent} !important;
}}
.big, .primary-value {{ font-size: {xlarge}px !important; font-weight: 700 !important; }}
"""
    if options.scheme.is_greyscale:
        return f"""
/* Multi-level greyscale: real tonal hierarchy is available, so let secondary
   text and chart fills use it. Still no shadows. */
.secondary, .label {{ color: var(--secondary-text-color) !important; }}
.big, .primary-value {{ font-size: {huge}px !important; }}
"""
    return f"""
/* Full-colour panel. The gamut is tiny and refreshes are slow, so colour is
   for categorical meaning only - never for gradients or photographs of UI. */
.alert, .warning, .error {{ color: {accent} !important; }}
.big, .primary-value {{ font-size: {xlarge}px !important; font-weight: 700 !important; }}
"""


__all__ = ["ThemeOptions", "TypeScale", "build_css", "mm_to_px", "EINK_FONT_STACK"]
