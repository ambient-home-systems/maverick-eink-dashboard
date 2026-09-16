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

import contextlib
import os
import re
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


@pytest.fixture
def one_display_config(tmp_path) -> Config:
    """One display, so the *editor* can be opened before the Add dialog is."""
    return Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "displays": [
                {"id": "kitchen", "panel": "generic-mono", "transport": {"type": "fake"}}
            ],
        }
    )


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


def _serve(config, legible_image, monkeypatch):
    renderer = _FakeRenderer(legible_image)
    monkeypatch.setattr(
        engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer
    )
    return _ServerThread(create_app(Application(config)))


@pytest.fixture
def app_server(empty_config, legible_image, monkeypatch):
    server = _serve(empty_config, legible_image, monkeypatch)
    base_url = server.start()
    try:
        yield base_url
    finally:
        server.stop()


@pytest.fixture
def populated_server(one_display_config, legible_image, monkeypatch):
    server = _serve(one_display_config, legible_image, monkeypatch)
    base_url = server.start()
    try:
        yield base_url
    finally:
        server.stop()


@contextlib.asynccontextmanager
async def _page(playwright):
    """A page on a Chromium launched the way `BrowserPool` launches one.

    The `MAVERICK_CHROMIUM_PATH` override is needed on aarch64, where
    Playwright ships no Chromium build of its own
    (`src/maverick/render/browser.py`).
    """
    launch: dict[str, object] = {}
    executable_path = os.environ.get("MAVERICK_CHROMIUM_PATH")
    if executable_path:
        launch["executable_path"] = executable_path
    browser = await playwright.chromium.launch(**launch)
    try:
        yield await browser.new_page()
    finally:
        await browser.close()


async def test_adding_a_display_through_the_form_renders_it(app_server):
    """The form end to end: fill it in, see the card the empty state promised,
    press Refresh, and watch the status pill go from "rendering" to "ok" the
    way a person watching the page would.

    The transport is picked by hand here, under Advanced, because the test
    needs the recording double rather than whatever `generic-mono` ships with
    — which is also what makes this the coverage for the fold opening and for
    a field inside it still being fillable.
    """
    async with async_playwright() as playwright:
        async with _page(playwright) as page:
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
            await page.locator("#add-advanced summary").click()
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


async def test_a_name_and_a_panel_are_enough_to_add_a_display(app_server):
    """Nothing is opened, nothing is chosen: a name, a panel and Add.

    This is the claim the form was rebuilt around — that adding a display
    needs what only the user knows and nothing else. The id comes from the
    name, the dashboard from the field's own value, the schedule from the
    Refresh picker's default and the transport from the panel
    (`DisplayConfig.transport_type`, `src/maverick/config.py`), which is what
    the card then reports delivering over.
    """
    async with async_playwright() as playwright:
        async with _page(playwright) as page:
            await page.goto(app_server)
            await page.click("#add-display-btn")
            await page.wait_for_selector(
                '#add-panel option[value="opendisplay-solum-4in2-bwr"]', state="attached"
            )
            await page.fill("#add-name", "Hallway Tag")
            await page.select_option("#add-panel", "opendisplay-solum-4in2-bwr")
            await page.click("#add-submit")

            card = page.locator("section.card").filter(has_text="hallway-tag")
            await card.wait_for(state="visible", timeout=10000)
            # The BLE tag's own transport, never typed by anyone.
            await expect(card.locator(".card-transport")).to_have_text("opendisplay")
            await expect(card.locator(".card-schedule")).to_contain_text("5m")


async def test_the_panel_picker_fills_after_the_editor_has_been_opened(populated_server):
    """A regression: the Add dialog's selects were empty for the rest of the
    page's life once an editor had been opened.

    Both are built from the same cached `formData`, and `ensureAddDialogData`
    guarded on *that* rather than on whether this dialog had been filled in —
    so opening an editor first satisfied the guard and `populateAddDialog`
    never ran. Nothing failed and nothing was logged; the panel picker was
    simply empty, which is what a user reported.
    """
    async with async_playwright() as playwright:
        async with _page(playwright) as page:
            await page.goto(populated_server)
            card = page.locator("section.card").filter(has_text="kitchen")
            await card.wait_for(state="visible", timeout=10000)

            await card.locator(".act-edit").click()
            await page.wait_for_selector(
                '#editor-panel option[value="generic-mono"]', state="attached", timeout=10000
            )
            await page.locator("#editor .act-close").click()

            await page.click("#add-display-btn")
            await page.wait_for_selector(
                '#add-panel option[value="generic-mono"]', state="attached", timeout=10000
            )
            assert await page.locator("#add-panel option").count() > 1
            assert await page.locator("#add-transport option").count() > 1


async def test_the_dashboard_starter_generates_yaml_on_the_card(populated_server):
    """The answer to "where do I learn to build a dashboard for this thing".

    The card's own disclosure, fetched from
    `GET /api/displays/{id}/dashboard.yaml` on first open, with the panel's
    line budget written into it and a link to the design guide beside it.
    """
    async with async_playwright() as playwright:
        async with _page(playwright) as page:
            await page.goto(populated_server)
            card = page.locator("section.card").filter(has_text="kitchen")
            await card.wait_for(state="visible", timeout=10000)

            starter = card.locator("details.starter")
            await starter.locator("summary").click()
            yaml_box = starter.locator(".starter-yaml")
            await expect(yaml_box).to_contain_text("views:", timeout=10000)
            text = await yaml_box.text_content()

            # Sized for `generic-mono` — 800×480 at 124 dpi, the same three
            # columns and twenty-one lines the design guide's table gives it.
            assert "columns: 3" in text
            assert "21 lines of body text" in text
            # And it teaches rather than only emitting: the cards it refused.
            assert "NOT USED" in text

            await expect(starter.locator(".starter-guide")).to_have_attribute(
                "href", re.compile(r"design-guide\.md$")
            )
