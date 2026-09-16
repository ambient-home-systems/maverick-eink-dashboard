"""The pre-quantisation screenshot, kept next to the quantised frame.

`render.debug_artifacts` (`src/maverick/config.py`) writes the full-resolution
capture to `<data_dir>/debug/<id>/`, but nothing serves it — seeing it means
Samba or SSH. `render.keep_screenshot` (on by default) is the UI-facing
answer: `Engine.render` downscales the capture to panel resolution with
`eink.fit_to_panel` and stores it in `FrameStore` as a fourth file,
`<id>.screenshot.png`, independent of whatever the transport does with the
frame itself — only `HttpPullTransport.deliver`
(`src/maverick/transports/pull.py`) ever calls `FrameStore.put`, so these
tests use the `fake` transport throughout to prove the screenshot does not
depend on that call.

Built on the same `make_engine`/`minimal_config` doubles as
tests/test_runtime_displays.py and the same HTTP harness as
tests/test_display_api.py: nothing here opens a browser, a broker or a socket.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO

import pytest
from conftest import DISPLAY_ID, PANEL_SIZE, FakeRenderer
from fastapi.testclient import TestClient
from PIL import Image

from maverick import engine as engine_module
from maverick.app import Application
from maverick.config import Config
from maverick.engine import FrameStore
from maverick.server.api import create_app

# --------------------------------------------------------------------------- #
# FrameStore, directly
# --------------------------------------------------------------------------- #


def test_put_and_get_screenshot_round_trip(tmp_path) -> None:
    store = FrameStore(tmp_path / "frames")
    image = Image.new("RGB", PANEL_SIZE, (255, 255, 255))

    store.put_screenshot(DISPLAY_ID, image)

    data = store.get_screenshot(DISPLAY_ID)
    assert data is not None
    assert Image.open(BytesIO(data)).size == PANEL_SIZE
    assert (tmp_path / "frames" / f"{DISPLAY_ID}.screenshot.png").exists()
    assert not list((tmp_path / "frames").glob("*.tmp")), "write-then-rename should leave none"


def test_get_screenshot_is_none_before_anything_is_put(tmp_path) -> None:
    store = FrameStore(tmp_path / "frames")
    assert store.get_screenshot(DISPLAY_ID) is None


def test_load_restores_the_screenshot_after_a_simulated_restart(tmp_path) -> None:
    directory = tmp_path / "frames"
    image = Image.new("RGB", PANEL_SIZE, (10, 20, 30))
    FrameStore(directory).put_screenshot(DISPLAY_ID, image)

    restarted = FrameStore(directory)
    assert restarted.get_screenshot(DISPLAY_ID) is None, "nothing loaded yet"
    restarted.load([DISPLAY_ID])

    data = restarted.get_screenshot(DISPLAY_ID)
    assert data is not None
    assert Image.open(BytesIO(data)).size == PANEL_SIZE


def test_remove_deletes_the_screenshot(tmp_path) -> None:
    directory = tmp_path / "frames"
    store = FrameStore(directory)
    store.put_screenshot(DISPLAY_ID, Image.new("RGB", PANEL_SIZE, (0, 0, 0)))

    store.remove(DISPLAY_ID)

    assert store.get_screenshot(DISPLAY_ID) is None
    assert not (directory / f"{DISPLAY_ID}.screenshot.png").exists()


# --------------------------------------------------------------------------- #
# Engine.render
# --------------------------------------------------------------------------- #


async def test_render_stores_the_screenshot_at_panel_resolution(make_engine, legible_image) -> None:
    """`fake` pushes and never calls `FrameStore.put` — the screenshot must not
    depend on that: it is `Engine.render` itself that stores it."""
    harness = await make_engine(legible_image)

    await harness.engine.render(DISPLAY_ID)

    data = harness.engine.frames.get_screenshot(DISPLAY_ID)
    assert data is not None
    assert Image.open(BytesIO(data)).size == PANEL_SIZE


async def test_keep_screenshot_false_stores_nothing(
    make_engine, legible_image, minimal_config
) -> None:
    minimal_config.display(DISPLAY_ID).render.keep_screenshot = False
    harness = await make_engine(legible_image)

    await harness.engine.render(DISPLAY_ID)

    assert harness.engine.frames.get_screenshot(DISPLAY_ID) is None
    frames_dir = harness.engine.data_dir / "frames"
    assert list(frames_dir.glob(f"{DISPLAY_ID}.screenshot.png")) == []


# --------------------------------------------------------------------------- #
# GET /api/displays/{id}/screenshot.png
# --------------------------------------------------------------------------- #


@dataclass
class ApiHarness:
    client: TestClient
    app: Application


@pytest.fixture
def harness(minimal_config: Config, monkeypatch, legible_image):
    minimal_config.display(DISPLAY_ID).schedule.render_on_start = False
    renderer = FakeRenderer(legible_image)
    monkeypatch.setattr(
        engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer
    )
    app = Application(minimal_config)
    with TestClient(create_app(app)) as client:
        yield ApiHarness(client=client, app=app)


def test_screenshot_route_404s_before_the_first_render(harness: ApiHarness) -> None:
    response = harness.client.get(f"/api/displays/{DISPLAY_ID}/screenshot.png")
    assert response.status_code == 404


def test_screenshot_route_serves_the_stored_screenshot(harness: ApiHarness) -> None:
    render = harness.client.post(f"/api/displays/{DISPLAY_ID}/render")
    assert render.status_code == 200

    response = harness.client.get(f"/api/displays/{DISPLAY_ID}/screenshot.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert Image.open(BytesIO(response.content)).size == PANEL_SIZE


def test_screenshot_route_stays_404_with_keep_screenshot_false(
    minimal_config: Config, monkeypatch, legible_image
) -> None:
    minimal_config.display(DISPLAY_ID).schedule.render_on_start = False
    minimal_config.display(DISPLAY_ID).render.keep_screenshot = False
    monkeypatch.setattr(
        engine_module,
        "DashboardRenderer",
        lambda ha, pool, tokens=None: FakeRenderer(legible_image),
    )
    app = Application(minimal_config)
    with TestClient(create_app(app)) as client:
        assert client.post(f"/api/displays/{DISPLAY_ID}/render").status_code == 200
        response = client.get(f"/api/displays/{DISPLAY_ID}/screenshot.png")

    assert response.status_code == 404


def test_screenshot_route_404s_for_an_unknown_display(harness: ApiHarness) -> None:
    response = harness.client.get("/api/displays/nowhere/screenshot.png")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# POST /api/displays/preview
# --------------------------------------------------------------------------- #


def test_preview_returns_the_screenshot_alongside_the_frame(harness: ApiHarness) -> None:
    width, height = PANEL_SIZE
    body = {
        "id": "candidate",
        "panel": "generic-mono",
        "width": width,
        "height": height,
        "frame_format": "png",
        "transport": {"type": "fake"},
    }

    response = harness.client.post("/api/displays/preview", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert "screenshot_png" in payload
    decoded = base64.b64decode(payload["screenshot_png"])
    assert Image.open(BytesIO(decoded)).size == PANEL_SIZE
    # A preview never touches FrameStore, screenshot included.
    assert harness.app.engine.frames.get_screenshot("candidate") is None
