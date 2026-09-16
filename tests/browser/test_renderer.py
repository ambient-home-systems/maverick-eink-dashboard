"""`DashboardRenderer` against a real Chromium.

docs/architecture.md, "What a prototype proved", names two findings that
"need a browser to fail in": the auth bundle has to be seeded before the
dashboard's own first script runs, and the injected stylesheet has to be
adopted into every shadow root, including ones created after load. Neither
can be reproduced by the rest of the suite, which stubs the renderer entirely
(`tests/conftest.py`). This module drives the real thing against the static
fixture pages in `tests/browser/fixtures/` — no Home Assistant involved, just
pages that reproduce the two shapes.
"""

from __future__ import annotations

import functools
import http.server
import json
import threading
import time
from pathlib import Path

import pytest
from playwright.async_api import Page

from maverick.config import DisplayConfig, HomeAssistantConfig, RenderConfig
from maverick.render.browser import BrowserPool
from maverick.render.dashboard import DashboardRenderer, RenderError

FIXTURES = Path(__file__).parent / "fixtures"


class _FixtureHandler(http.server.SimpleHTTPRequestHandler):
    """Serves `fixtures/`, delaying `slow.png` so `wait_for_images` has
    something to actually wait for."""

    def do_GET(self) -> None:  # noqa: N802 - stdlib's own name
        if self.path.startswith("/slow.png"):
            time.sleep(0.25)
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002, N802
        pass  # the default logs every request to stderr


@pytest.fixture(scope="module")
def fixture_server():
    handler = functools.partial(_FixtureHandler, directory=str(FIXTURES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture
async def pool():
    browser_pool = BrowserPool(max_concurrent=2)
    try:
        yield browser_pool
    finally:
        await browser_pool.stop()


def _display(dashboard: str, **render_kwargs) -> DisplayConfig:
    render_kwargs.setdefault("wait_for_selector", "home-assistant")
    render_kwargs.setdefault("settle", 1.0)
    render_kwargs.setdefault("timeout", 10.0)
    config = DisplayConfig(
        id="fixture",
        panel="generic-mono",
        dashboard=dashboard,
        width=300,
        height=200,
        render=RenderConfig(**render_kwargs),
    )
    return config.resolved()


async def test_the_stylesheet_is_adopted_into_every_shadow_root(fixture_server, pool, monkeypatch):
    """The shadow-DOM finding: a sheet on `document.head` reaches nothing
    inside a web component, so it has to be adopted into every shadow root —
    the document's own, `<home-assistant>`'s, and the card's that attaches
    500ms after load.
    """
    ha = HomeAssistantConfig(url=fixture_server, token="fixture-token")
    display = _display("/dashboard.html")
    renderer = DashboardRenderer(ha, pool)

    # `render()` closes its page once it has the screenshot, so what the page
    # looked like has to be captured just before that happens — the spy
    # intercepts the same `Page.screenshot` call `render()` itself makes.
    captured: dict[str, object] = {}
    original_screenshot = Page.screenshot

    async def spy(self: Page, *args, **kwargs):
        captured["adopted"] = await self.evaluate(
            """() => {
                const ha = document.querySelector('home-assistant');
                const haRoot = ha && ha.shadowRoot;
                const card = haRoot && haRoot.querySelector('ha-card');
                const cardRoot = card && card.shadowRoot;
                return {
                    document: document.adoptedStyleSheets.length > 0,
                    homeAssistant: !!(haRoot && haRoot.adoptedStyleSheets.length > 0),
                    card: !!(cardRoot && cardRoot.adoptedStyleSheets.length > 0),
                };
            }"""
        )
        return await original_screenshot(self, *args, **kwargs)

    monkeypatch.setattr(Page, "screenshot", spy)
    await renderer.render(display)

    assert captured["adopted"] == {"document": True, "homeAssistant": True, "card": True}


async def test_the_auth_bundle_is_seeded_before_the_page_s_first_script(
    fixture_server, pool, monkeypatch
):
    """The auth finding: `localStorage.hassTokens` has to exist before the
    dashboard's own first line of JavaScript runs, or the frontend has
    already decided to redirect to the login page by the time it appears.
    `dashboard.html`'s first script records what it saw into a global, which
    this reads back once the render is done with the page.
    """
    ha = HomeAssistantConfig(url=fixture_server, token="fixture-token")
    display = _display("/dashboard.html")
    renderer = DashboardRenderer(ha, pool)

    captured: dict[str, object] = {}
    original_screenshot = Page.screenshot

    async def spy(self: Page, *args, **kwargs):
        captured["seen"] = await self.evaluate("() => window.__maverickSeenAuth")
        return await original_screenshot(self, *args, **kwargs)

    monkeypatch.setattr(Page, "screenshot", spy)
    await renderer.render(display)

    seen = captured["seen"]
    assert seen, "dashboard.html's first script saw no localStorage.hassTokens"
    bundle = json.loads(seen)
    assert bundle["hassUrl"] == ha.render_url
    assert bundle["access_token"] == "fixture-token"


async def test_the_login_page_is_rejected_and_its_context_dropped(fixture_server, pool):
    """`_verify_authenticated` blocks delivery rather than screenshotting the
    login form, and drops the context so the next render does not reuse a
    session Home Assistant has already rejected.
    """
    ha = HomeAssistantConfig(url=fixture_server, token="fixture-token")
    display = _display("/login.html")
    renderer = DashboardRenderer(ha, pool)

    with pytest.raises(RenderError, match="showed the login form"):
        await renderer.render(display)

    assert pool._contexts == {}, "the context that saw the login form was not dropped"


async def test_the_image_is_the_viewport_size_times_supersample(fixture_server, pool):
    ha = HomeAssistantConfig(url=fixture_server, token="fixture-token")
    display = _display("/dashboard.html", supersample=2)

    renderer = DashboardRenderer(ha, pool)
    result = await renderer.render(display)

    assert result.viewport == (300, 200)
    assert result.scale == 2.0
    assert result.image.size == (600, 400)
