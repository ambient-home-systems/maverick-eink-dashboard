"""Browser-level coverage for the Phase 2 setup UI.

Nothing in `tests/test_setup_ui.py` drives a browser — it can only check that
the markup and the endpoints `static/app.js` calls exist, not that clicking
through the Add display dialog actually produces a card, or that a render
followed through the poll actually reaches "ok". This does that against a
real FastAPI app started under uvicorn, with a renderer double standing in
for Chromium — the same idea as `tests/conftest.py`'s `FakeRenderer`, kept
local here rather than imported: a file under `tests/browser/` sits in a
different directory than `tests/conftest.py`, and having its own
`conftest.py` (for the Chromium gate) means a plain `from conftest import
...` resolves to the wrong one.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

import pytest
import uvicorn
from playwright.async_api import async_playwright, expect

from maverick import engine as engine_module
from maverick.app import Application
from maverick.config import Config, ResolvedDisplay
from maverick.render.dashboard import RenderResult
from maverick.server.api import create_app


@dataclass
class _FakeRenderer:
    """Stands in for `DashboardRenderer`: no browser, one prepared image."""

    image: object
    calls: int = 0

    async def render(self, display: ResolvedDisplay) -> RenderResult:
        self.calls += 1
        return RenderResult(
            image=self.image,
            url=f"fake://{display.id}",
            duration_s=0.0,
            viewport=(display.width, display.height),
            scale=1.0,
        )


@pytest.fixture
def empty_config(tmp_path) -> Config:
    """No displays: the page opens on the empty state, so the Add display
    button under test is the one that replaces it."""
    return Config.model_validate({"data_dir": str(tmp_path / "data")})


class _ServerThread:
    """Runs a FastAPI app under uvicorn in a background thread.

    `Application.start`/`stop` are wired to the app's own startup/shutdown
    events (`create_app`), so starting and stopping this is starting and
    stopping the whole service, engine included.
    """

    def __init__(self, app) -> None:
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        self.server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> str:
        self._thread.start()
        while not self.server.started:
            pass
        port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}/"

    def stop(self) -> None:
        self.server.should_exit = True
        self._thread.join(timeout=10)


@pytest.fixture
def app_server(empty_config, legible_image, monkeypatch):
    renderer = _FakeRenderer(legible_image)
    monkeypatch.setattr(
        engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer
    )
    application = Application(empty_config)
    server = _ServerThread(create_app(application))
    base_url = server.start()
    try:
        yield base_url
    finally:
        server.stop()


async def test_adding_a_display_through_the_form_renders_it(app_server):
    """The form from P2.2, end to end: fill it in, see the card the empty
    state promised, press Refresh, and watch the status pill go from
    "rendering" to "ok" the way a person watching the page would.
    """
    async with async_playwright() as playwright:
        # The same override `BrowserPool` accepts (`src/maverick/render/browser.py`):
        # needed on aarch64, where Playwright ships no Chromium build of its own.
        launch: dict[str, object] = {}
        executable_path = os.environ.get("MAVERICK_CHROMIUM_PATH")
        if executable_path:
            launch["executable_path"] = executable_path
        browser = await playwright.chromium.launch(**launch)
        try:
            page = await browser.new_page()
            await page.goto(app_server)

            await page.click("#add-display-btn")
            # The panel <select> is populated from `GET /api/panels` once the
            # dialog opens; waiting for an option is waiting for that fetch.
            # An <option> never reports "visible" while its <select> is
            # closed, so "attached" is the state that actually resolves.
            await page.wait_for_selector(
                '#add-panel option[value="generic-mono"]', state="attached"
            )
            await page.fill("#add-name", "Office")
            await page.select_option("#add-panel", "generic-mono")
            await page.select_option("#add-transport", "fake")
            await page.click("#add-submit")

            card = page.locator("section.card").filter(has_text="office")
            await card.wait_for(state="visible", timeout=10000)
            await expect(card.locator(".card-name")).to_have_text("Office")

            await card.locator(".act-render").click()
            # `?wait=false` answers 202 immediately and renders in the
            # background; the page shows "rendering" from its own optimistic
            # state for up to ten seconds (`PENDING_GRACE_MS`, `app.js`) even
            # once the render has actually finished, so "ok" is worth a
            # generous timeout rather than a flaky short one.
            await expect(card.locator(".pill")).to_have_text("ok", timeout=20000)
        finally:
            await browser.close()
