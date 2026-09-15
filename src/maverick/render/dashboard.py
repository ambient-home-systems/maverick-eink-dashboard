"""Render a Home Assistant dashboard to a screenshot.

Two things here are not obvious and are where most home-grown versions of this
tool break.

**Authentication.** The Home Assistant frontend does not accept an
``Authorization`` header; it reads a token bundle out of ``localStorage`` under
the key ``hassTokens``. A long-lived access token works, but only if the bundle
is well-formed and ``hassUrl`` exactly matches the origin being loaded — a
trailing slash or an http/https mismatch produces a silent redirect to the
login screen, which then screenshots as a blank frame. The bundle is installed
via an *init script* so it is present before the frontend's first line of
JavaScript runs, rather than after a navigation that has already bounced.

**Shadow DOM.** The frontend is web components throughout, so a stylesheet
appended to ``document.head`` reaches almost nothing. Custom properties do
inherit across shadow boundaries — that is how HA themes work — but the flatten
and typography rules are ordinary declarations and must be adopted into every
shadow root individually, including ones created after load.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from io import BytesIO

from PIL import Image

from ..config import DisplayConfig, HomeAssistantConfig, ResolvedDisplay
from ..eink.theme import ThemeOptions, TypeScale, build_css
from ..ha.auth import TokenSource, build_token_source
from .browser import BrowserPool

log = logging.getLogger(__name__)


class RenderError(RuntimeError):
    """Raised when a dashboard could not be captured."""


# Installed before any page script. Ten years of validity because the token is
# long-lived; the frontend refuses to use a bundle it believes has expired.
_AUTH_SCRIPT = """
(() => {
  const tokens = %(tokens)s;
  try {
    window.localStorage.setItem('hassTokens', JSON.stringify(tokens));
    // Force the light theme and suppress the onboarding/what's-new dialogs,
    // any of which will otherwise sit on top of the dashboard.
    window.localStorage.setItem('selectedTheme', JSON.stringify({dark: false}));
    window.localStorage.setItem('dockedSidebar', '"always_hidden"');
    window.localStorage.setItem('sidebarPanelOrder', '[]');
  } catch (e) {
    console.error('maverick: could not seed auth', e);
  }
})();
"""

# Adopts a stylesheet into the document and every shadow root, then keeps
# adopting into shadow roots created later. Patching attachShadow is the only
# reliable way to catch roots that appear while cards lazily render.
_STYLE_SCRIPT = """
(() => {
  const CSS = %(css)s;
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(CSS);

  const adopt = (root) => {
    try {
      if (!root || root.__maverickStyled) return;
      root.__maverickStyled = true;
      root.adoptedStyleSheets = [...(root.adoptedStyleSheets || []), sheet];
    } catch (e) { /* closed or cross-origin root */ }
  };

  const walk = (root) => {
    adopt(root);
    const nodes = root.querySelectorAll ? root.querySelectorAll('*') : [];
    for (const node of nodes) if (node.shadowRoot) walk(node.shadowRoot);
  };

  const original = Element.prototype.attachShadow;
  Element.prototype.attachShadow = function (init) {
    const root = original.call(this, init);
    adopt(root);
    return root;
  };

  window.__maverickRestyle = () => walk(document);
  const run = () => window.__maverickRestyle();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', run);
  } else {
    run();
  }
})();
"""

# Resolves once every image on the page has finished decoding, or after a cap.
# Weather and camera cards are consistently the last thing to settle.
_IMAGES_SCRIPT = """
(async (timeoutMs) => {
  const deadline = Date.now() + timeoutMs;
  const collect = (root, out) => {
    for (const img of root.querySelectorAll('img')) out.push(img);
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) collect(el.shadowRoot, out);
    return out;
  };
  while (Date.now() < deadline) {
    const imgs = collect(document, []);
    if (imgs.every((i) => i.complete && i.naturalWidth > 0)) break;
    await new Promise((r) => setTimeout(r, 120));
  }
  if (document.fonts && document.fonts.ready) await document.fonts.ready;
})
"""


@dataclass
class RenderResult:
    image: Image.Image
    url: str
    duration_s: float
    viewport: tuple[int, int]
    scale: float


async def build_auth_bundle(
    ha: HomeAssistantConfig, tokens: TokenSource | None = None
) -> dict[str, object]:
    """The token bundle the frontend expects in ``localStorage.hassTokens``.

    The credential half comes from the token source, because only it knows
    whether the token expires in thirty minutes or a decade. A linked account
    also puts its ``clientId`` and ``refresh_token`` in the bundle, which lets
    the frontend renew the session by itself rather than bouncing to the login
    screen if a render outlives the access token.
    """
    source = tokens or build_token_source(ha)
    if source is None:
        raise RenderError(
            "No Home Assistant credential configured. Open Maverick's setup UI "
            "and use 'Link with Home Assistant', or set home_assistant.token."
        )
    return {
        "token_type": "Bearer",
        "hassUrl": ha.render_url,
        **await source.bundle_fields(),
    }


def build_theme_css(display: ResolvedDisplay) -> str:
    """Compose the injected stylesheet for a display."""
    theme = display.config.theme
    if not theme.enabled:
        return theme.extra_css

    extra = theme.extra_css
    if theme.css_file:
        from pathlib import Path

        path = Path(theme.css_file)
        if not path.exists():
            raise RenderError(f"theme.css_file not found: {path}")
        extra = f"{extra}\n{path.read_text(encoding='utf-8')}"

    options = ThemeOptions(
        dpi=display.dpi,
        scheme=display.color_scheme,
        type_scale=TypeScale(body_mm=theme.body_mm, ratio=theme.scale_ratio),
        min_font_weight=theme.min_font_weight,
        strong_font_weight=theme.strong_font_weight,
        rule_mm=theme.rule_mm,
        radius_mm=theme.radius_mm,
        hide_chrome=theme.hide_chrome,
        use_spot_colour=theme.use_spot_colour,
        # render.zoom composes with the dpi-derived zoom rather than fighting it.
        zoom_multiplier=display.config.render.zoom,
        letter_spacing_em=theme.letter_spacing_em,
        palette_overrides=dict(display.config.image.palette_overrides),
        extra_css=extra,
    )
    if theme.font_stack:
        options.font_stack = theme.font_stack
    return build_css(options)


def needs_zoom_style_tag(config: DisplayConfig) -> bool:
    """Whether the renderer has to apply ``render.zoom`` itself.

    With the theme enabled, :func:`build_css` folds ``render.zoom`` into the
    stylesheet's own root zoom as ``zoom_multiplier``, and a second zoom here
    would compound it. With the theme disabled, :func:`build_theme_css` returns
    ``theme.extra_css`` verbatim and nothing applies the zoom at all, so the
    renderer adds a style tag of its own.

    The condition is ``theme.enabled`` and nothing else. Keying it off "the
    composed stylesheet came back empty" meant that disabling the theme while
    keeping any ``extra_css`` dropped ``render.zoom`` on the floor.
    """
    return config.render.zoom != 1.0 and not config.theme.enabled


_ABSOLUTE_URL = re.compile(r"^[a-z][a-z0-9+.-]*://", re.I)


def resolve_url(ha: HomeAssistantConfig, dashboard: str) -> str:
    """Accept either a dashboard path or a fully qualified URL.

    Any scheme counts as absolute, not just http(s): pointing a display at a
    ``file://`` page is how you preview a static mock-up against a real panel.
    """
    if _ABSOLUTE_URL.match(dashboard):
        return dashboard
    return f"{ha.render_url}/{dashboard.lstrip('/')}"


class DashboardRenderer:
    """Captures dashboards for every configured display."""

    def __init__(
        self,
        ha: HomeAssistantConfig,
        pool: BrowserPool,
        tokens: TokenSource | None = None,
    ) -> None:
        self._ha = ha
        self._pool = pool
        # Shared with the engine's REST client so one refresh serves both.
        self._tokens = tokens or build_token_source(ha)

    def viewport_for(self, display: ResolvedDisplay) -> tuple[int, int]:
        """Browser viewport in *pre-rotation* logical pixels.

        A portrait panel mounted sideways still wants a landscape browser
        viewport; the pipeline rotates afterwards.
        """
        render = display.config.render
        if render.viewport_width and render.viewport_height:
            return render.viewport_width, render.viewport_height
        if display.rotation in (90, 270):
            return display.height, display.width
        return display.width, display.height

    async def render(self, display: ResolvedDisplay) -> RenderResult:
        config: DisplayConfig = display.config
        render = config.render
        url = resolve_url(self._ha, config.dashboard)
        viewport = self.viewport_for(display)
        css = build_theme_css(display)

        init_scripts = [_STYLE_SCRIPT % {"css": json.dumps(css)}] if css else []

        # Context key covers everything fixed at context-creation time, plus a
        # digest of the scripts so a config change never reuses a stale context.
        # The auth bundle is deliberately *not* part of it: a linked account's
        # access token rotates every half hour, and keying on it would strand a
        # dead context in the pool on every refresh.
        key = f"{display.id}:{viewport}:{render.supersample}:{hash(tuple(init_scripts))}"

        started = time.perf_counter()
        async with self._pool.page(
            key,
            viewport,
            scale=float(render.supersample),
            init_scripts=init_scripts,
            ignore_https_errors=not self._ha.verify_ssl,
        ) as page:
            # Seeded per page instead, which still runs before the frontend's
            # first line of JavaScript but carries a token fetched just now.
            bundle = await build_auth_bundle(self._ha, self._tokens)
            await page.add_init_script(_AUTH_SCRIPT % {"tokens": json.dumps(bundle)})

            timeout_ms = int(render.timeout * 1000)
            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the user
                # networkidle never settles on dashboards with a live camera or
                # a streaming graph. Fall back rather than fail the render.
                log.debug("networkidle timed out for %s (%s); falling back", display.id, exc)
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            await self._verify_authenticated(page, display, key)

            if needs_zoom_style_tag(config):
                await page.add_style_tag(content=f"html {{ zoom: {render.zoom}; }}")

            selector = render.wait_for_selector or "home-assistant"
            try:
                await page.wait_for_selector(selector, timeout=timeout_ms, state="attached")
            except Exception as exc:  # noqa: BLE001
                raise RenderError(
                    f"[{display.id}] never saw {selector!r} at {url}. "
                    "If this is not a Home Assistant dashboard, set "
                    "render.wait_for_selector to something on the page."
                ) from exc

            if css:
                # Re-walk: lazily rendered cards attach shadow roots after the
                # first pass, and the patched attachShadow only covers new ones.
                await page.evaluate("() => window.__maverickRestyle && window.__maverickRestyle()")

            if render.wait_for_images:
                await page.evaluate(_IMAGES_SCRIPT, min(timeout_ms, 15000))

            if render.settle:
                await page.wait_for_timeout(int(render.settle * 1000))

            if css:
                await page.evaluate("() => window.__maverickRestyle && window.__maverickRestyle()")

            target = page
            if render.crop_to_selector:
                element = await page.query_selector(render.crop_to_selector)
                if element is None:
                    raise RenderError(
                        f"[{display.id}] crop_to_selector "
                        f"{render.crop_to_selector!r} matched nothing."
                    )
                target = element

            png = await target.screenshot(type="png", animations="disabled")

        image = Image.open(BytesIO(png)).convert("RGB")
        duration = time.perf_counter() - started
        log.info(
            "[%s] rendered %s in %.2fs (%dx%d @%.0fx)",
            display.id, url, duration, image.width, image.height, render.supersample,
        )
        return RenderResult(
            image=image,
            url=url,
            duration_s=duration,
            viewport=viewport,
            scale=float(render.supersample),
        )

    async def _verify_authenticated(self, page, display: ResolvedDisplay, key: str) -> None:
        """Fail loudly on the login screen instead of silently shipping it.

        A blank or login-screen frame pushed to a battery panel can sit there
        for a day, so this is worth an explicit check.
        """
        landed = page.url
        if "/auth/authorize" in landed or landed.rstrip("/").endswith("/lovelace/login"):
            await self._pool.drop_context(key)
            raise RenderError(
                f"[{display.id}] Home Assistant redirected to the login page. "
                "The access token is missing, expired or was issued for a "
                "different URL — home_assistant.url must match the origin the "
                "token was created on, scheme and port included."
            )
        has_login = await page.evaluate(
            "() => !!document.querySelector('ha-authorize, ha-auth-flow')"
        )
        if has_login:
            await self._pool.drop_context(key)
            raise RenderError(
                f"[{display.id}] Home Assistant showed the login form. "
                "The credential was rejected: re-link the account from the "
                "setup UI, or check home_assistant.token is a long-lived "
                "access token."
            )


__all__ = ["DashboardRenderer", "RenderResult", "RenderError", "build_auth_bundle", "resolve_url"]
