"""The HTTP surface for creating, editing and previewing displays.

`Application.add_display`, `update_display`, `remove_display` and
`Engine.render_candidate` exist (`src/maverick/app.py`, `src/maverick/engine.py`)
but nothing before this reached them over HTTP: the only mutating routes were
the two that render. These tests drive the new routes through
`fastapi.testclient.TestClient`, on the same `make_engine`/`make_app` doubles as
tests/test_runtime_displays.py — `FakeTransport` records deliveries and
`FakeRenderer` stands in for Chromium — so nothing here opens a browser, a
broker or a socket.

The one test that needs true concurrency (a render held open while a second
request asks about it) cannot use the synchronous `TestClient`, whose requests
and the background render task would run on different threads: `httpx`'s
`AsyncClient` over `ASGITransport` keeps everything on the single event loop
`pytest-asyncio` already provides, the same way tests/test_runtime_displays.py
coordinates a `GatedRenderer` with `asyncio.Event`.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import pytest
from conftest import DISPLAY_ID, PANEL_SIZE, FakeRenderer
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from PIL import Image

from maverick import engine as engine_module
from maverick.app import Application
from maverick.config import Config
from maverick.server.api import create_app

# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


def display_body(display_id: str, **overrides: Any) -> dict[str, Any]:
    """A display request body the `fake` transport can drive."""
    width, height = PANEL_SIZE
    body: dict[str, Any] = {
        "id": display_id,
        "panel": "generic-mono",
        "width": width,
        "height": height,
        "frame_format": "png",
        "transport": {"type": "fake"},
    }
    body.update(overrides)
    return body


class GatedRenderer(FakeRenderer):
    """A renderer that stops inside `render` until it is let go.

    Copied from tests/test_runtime_displays.py rather than imported: it is the
    only thing that file exports that this one needs, and importing a sibling
    test module for one class reads worse than the few lines it costs.
    """

    def __init__(self, image: Image.Image) -> None:
        super().__init__(image)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def render(self, display):  # type: ignore[no-untyped-def]
        self.entered.set()
        await self.release.wait()
        return await super().render(display)


@dataclass
class ApiHarness:
    client: TestClient
    app: Application
    renderer: FakeRenderer

    def transport(self, display_id: str) -> Any:
        return self.app.engine._transports[display_id]


@pytest.fixture
def harness(minimal_config: Config, monkeypatch, legible_image):
    """A started `Application`, reached over HTTP.

    The lifespan is real (`with TestClient(...)`), so `_startup` calls
    `application.start()` for real — the engine, the scheduler and MQTT
    discovery are the genuine article — with only `DashboardRenderer` swapped
    for `FakeRenderer`, exactly as `make_app` does in
    tests/test_runtime_displays.py.
    """
    minimal_config.display(DISPLAY_ID).schedule.render_on_start = False
    renderer = FakeRenderer(legible_image)
    monkeypatch.setattr(
        engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer
    )
    app = Application(minimal_config)
    with TestClient(create_app(app)) as client:
        yield ApiHarness(client=client, app=app, renderer=renderer)


# --------------------------------------------------------------------------- #
# GET /api/schema/display
# --------------------------------------------------------------------------- #


def test_schema_display_carries_field_descriptions_and_transport_options(
    harness: ApiHarness,
) -> None:
    response = harness.client.get("/api/schema/display")
    assert response.status_code == 200
    body = response.json()
    assert "id" in body["properties"], "the JSON Schema for DisplayConfig should be at the top"
    assert body["properties"]["dashboard"]["description"], "help text is Field(description=...)"

    transports = body["transports"]
    assert {"fake", "http_pull", "file", "webhook", "mqtt", "opendisplay"} <= transports.keys()
    fake = transports["fake"]
    assert fake["description"]
    assert fake["pushes"] is True
    assert fake["options"] == {
        "pushes": "Whether the double behaves as a push transport (the default) or a pull one.",
    }


# --------------------------------------------------------------------------- #
# POST /api/displays
# --------------------------------------------------------------------------- #


def test_create_display_starts_it_and_returns_201(harness: ApiHarness) -> None:
    response = harness.client.post("/api/displays", json=display_body("study"))

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "study"
    assert harness.transport("study").started, "the display should actually be running"


def test_create_display_conflicts_on_a_duplicate_id(harness: ApiHarness) -> None:
    response = harness.client.post("/api/displays", json=display_body(DISPLAY_ID))

    assert response.status_code == 409
    assert DISPLAY_ID in response.json()["detail"]


def test_create_display_names_the_bad_field_in_a_422(harness: ApiHarness) -> None:
    """`panell` is a typo for `panel`; `extra='forbid'` (CLAUDE.md) must catch it."""
    response = harness.client.post("/api/displays", json={"id": "x", "panell": "generic-mono"})

    assert response.status_code == 422
    assert "panell" in response.text


def test_create_display_422s_for_an_unregistered_transport(harness: ApiHarness) -> None:
    """`TransportConfig.type` is a plain `str` (`extra='allow'`), so nothing but
    `get_transport` catches a typo in it — this must not surface as a 500."""
    response = harness.client.post(
        "/api/displays", json=display_body("study", transport={"type": "nonexistent"})
    )

    assert response.status_code == 422
    assert "study" not in {d.id for d in harness.app.config.displays}


# --------------------------------------------------------------------------- #
# PUT /api/displays/{id}
# --------------------------------------------------------------------------- #


def test_replace_display_applies_the_new_config(harness: ApiHarness) -> None:
    response = harness.client.put(
        f"/api/displays/{DISPLAY_ID}", json=display_body(DISPLAY_ID, name="Renamed Kitchen")
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed Kitchen"
    assert harness.app.config.display(DISPLAY_ID).name == "Renamed Kitchen"


def test_replace_display_rejects_a_body_id_that_does_not_match_the_path(
    harness: ApiHarness,
) -> None:
    response = harness.client.put(f"/api/displays/{DISPLAY_ID}", json=display_body("other"))

    assert response.status_code == 400
    assert harness.app.config.display(DISPLAY_ID).name == "Kitchen", "nothing should have changed"


def test_replace_display_404s_for_an_unknown_id(harness: ApiHarness) -> None:
    response = harness.client.put("/api/displays/nowhere", json=display_body("nowhere"))

    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# DELETE /api/displays/{id}
# --------------------------------------------------------------------------- #


def test_delete_display_removes_its_frames_from_disk(harness: ApiHarness) -> None:
    """`fake` only records deliveries in memory; `http_pull` is what fills the frame store."""
    body = display_body("study", transport={"type": "http_pull"})
    harness.client.post("/api/displays", json=body)
    harness.client.post("/api/displays/study/render")
    frames_dir = harness.app.engine.data_dir / "frames"
    assert list(frames_dir.glob("study.*")), "the render above should have written a frame"

    response = harness.client.delete("/api/displays/study")

    assert response.status_code == 204
    assert list(frames_dir.glob("study.*")) == []
    assert harness.client.get("/api/displays/study").status_code == 404


def test_delete_display_404s_for_an_unknown_id(harness: ApiHarness) -> None:
    assert harness.client.delete("/api/displays/nowhere").status_code == 404


# --------------------------------------------------------------------------- #
# POST /api/displays/{id}/schedule
# --------------------------------------------------------------------------- #


def test_schedule_toggle_pauses_and_resumes_at_runtime(harness: ApiHarness) -> None:
    response = harness.client.post(f"/api/displays/{DISPLAY_ID}/schedule", json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["schedule"]["enabled"] is False
    assert harness.app.scheduler.schedule_enabled[DISPLAY_ID] is False

    response = harness.client.post(f"/api/displays/{DISPLAY_ID}/schedule", json={"enabled": True})
    assert response.json()["schedule"]["enabled"] is True
    assert harness.app.scheduler.schedule_enabled[DISPLAY_ID] is True


def test_schedule_toggle_404s_for_an_unknown_id(harness: ApiHarness) -> None:
    response = harness.client.post("/api/displays/nowhere/schedule", json={"enabled": False})
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# POST /api/displays/preview
# --------------------------------------------------------------------------- #


def test_preview_renders_but_saves_nothing(harness: ApiHarness) -> None:
    frames_dir = harness.app.engine.data_dir / "frames"
    before = sorted(p.name for p in frames_dir.glob("*"))
    state_before = dict(harness.app.engine.states)

    response = harness.client.post("/api/displays/preview", json=display_body("candidate"))

    assert response.status_code == 200
    body = response.json()
    assert body["width"] == PANEL_SIZE[0]
    assert body["height"] == PANEL_SIZE[1]
    decoded = base64.b64decode(body["preview_png"])
    assert Image.open(BytesIO(decoded)).size == PANEL_SIZE
    assert body["lint"]["summary"] == "clean"
    assert body["render_s"] >= 0
    assert body["process_s"] >= 0

    assert "candidate" not in harness.app.engine.frames
    assert harness.app.engine.states.keys() == state_before.keys()
    assert sorted(p.name for p in frames_dir.glob("*")) == before
    assert not harness.app.store.exists(), "a preview must never touch the display store"


def test_preview_reports_lint_findings_without_a_non_200(
    minimal_config: Config, monkeypatch, blank_image: Image.Image
) -> None:
    """A blank frame is exactly what Preview exists to catch before it is saved."""
    monkeypatch.setattr(
        engine_module,
        "DashboardRenderer",
        lambda ha, pool, tokens=None: FakeRenderer(blank_image),
    )
    app = Application(minimal_config)
    with TestClient(create_app(app)) as client:
        response = client.post("/api/displays/preview", json=display_body("candidate"))

    assert response.status_code == 200
    assert response.json()["lint"]["issues"], "a blank frame should be reported, not silenced"


# --------------------------------------------------------------------------- #
# POST /api/displays/{id}/render?wait=false
# --------------------------------------------------------------------------- #


async def test_async_render_reports_rendering_until_the_render_completes(
    minimal_config: Config, monkeypatch, legible_image: Image.Image
) -> None:
    minimal_config.display(DISPLAY_ID).schedule.render_on_start = False
    renderer = GatedRenderer(legible_image)
    monkeypatch.setattr(
        engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer
    )
    app = Application(minimal_config)
    api = create_app(app)

    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        await app.start()
        try:
            queued = await client.post(f"/api/displays/{DISPLAY_ID}/render?wait=false")
            assert queued.status_code == 202
            assert queued.json() == {"display": DISPLAY_ID, "queued": True}

            await renderer.entered.wait()
            summary = await client.get(f"/api/displays/{DISPLAY_ID}")
            assert summary.json()["rendering"] is True

            again = await client.post(f"/api/displays/{DISPLAY_ID}/render?wait=false")
            assert again.status_code == 202
            assert again.json() == {
                "display": DISPLAY_ID,
                "queued": False,
                "already_rendering": True,
            }

            renderer.release.set()
            for _ in range(200):
                if not app.engine.is_rendering(DISPLAY_ID):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("the render never finished")

            summary = await client.get(f"/api/displays/{DISPLAY_ID}")
            assert summary.json()["rendering"] is False
            assert len(app.engine._transports[DISPLAY_ID].deliveries) == 1
        finally:
            await app.stop()


def test_sync_render_still_waits_for_the_result_by_default(harness: ApiHarness) -> None:
    """`wait=true` is the default, and keeps the response `rest_command` users depend on."""
    response = harness.client.post(f"/api/displays/{DISPLAY_ID}/render")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "queued" not in body


# --------------------------------------------------------------------------- #
# _display_summary: rendering, next_run_at, durations
# --------------------------------------------------------------------------- #


def test_next_run_at_is_null_for_a_manual_only_display(harness: ApiHarness) -> None:
    response = harness.client.get(f"/api/displays/{DISPLAY_ID}")
    assert response.json()["next_run_at"] is None


def test_next_run_at_is_set_for_an_interval_schedule(
    minimal_config: Config, monkeypatch, legible_image: Image.Image
) -> None:
    minimal_config.display(DISPLAY_ID).schedule.every = "5m"
    minimal_config.display(DISPLAY_ID).schedule.render_on_start = False
    monkeypatch.setattr(
        engine_module,
        "DashboardRenderer",
        lambda ha, pool, tokens=None: FakeRenderer(legible_image),
    )
    app = Application(minimal_config)
    with TestClient(create_app(app)) as client:
        response = client.get(f"/api/displays/{DISPLAY_ID}")

    assert response.json()["next_run_at"] is not None


def test_summary_gains_durations_after_a_render(harness: ApiHarness) -> None:
    before = harness.client.get(f"/api/displays/{DISPLAY_ID}").json()
    assert before["last_render_s"] is None
    assert before["last_total_s"] is None
    assert before["rendering"] is False

    harness.client.post(f"/api/displays/{DISPLAY_ID}/render")

    after = harness.client.get(f"/api/displays/{DISPLAY_ID}").json()
    assert after["last_render_s"] is not None
    assert after["last_total_s"] is not None
    assert after["rendering"] is False


def test_summary_carries_a_config_that_puts_straight_back(harness: ApiHarness) -> None:
    """The setup UI's Enable action flips one key and PUTs the rest untouched.

    A `PUT` replaces the whole configuration, so the UI needs the display as
    configured — which no route returned before this key existed, the summary
    being a resolved view. `dump_display` (`src/maverick/store.py`) is the same
    shortest form the display store writes, and this is the round trip that
    says it loads back.
    """
    display = harness.app.config.display(DISPLAY_ID)
    display.dashboard = "/lovelace-eink/kitchen"
    display.schedule.quiet_hours = "23:00-06:30"

    config = harness.client.get(f"/api/displays/{DISPLAY_ID}").json()["config"]
    assert config["dashboard"] == "/lovelace-eink/kitchen"

    response = harness.client.put(
        f"/api/displays/{DISPLAY_ID}", json={**config, "enabled": False}
    )

    assert response.status_code == 200, response.text
    after = harness.app.config.display(DISPLAY_ID)
    assert after.enabled is False
    # Everything the UI did not touch survived the round trip.
    assert after.dashboard == "/lovelace-eink/kitchen"
    assert after.schedule.quiet_hours == "23:00-06:30"
    assert after.transport.type == display.transport.type
