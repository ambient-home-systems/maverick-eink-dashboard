"""POST /api/render?force=true must actually force a full redraw.

The endpoint has always accepted `force`, but dropped it on the floor between
the API and the engine, so the documented "force a full refresh of every panel"
was a normal render that the unchanged-checksum shortcut could skip entirely.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from maverick.app import Application
from maverick.config import Config
from maverick.engine import Engine, RenderOutcome
from maverick.server.api import create_app

TOKEN = "s3cret-token"


@pytest.fixture
def calls(monkeypatch) -> list[dict[str, object]]:
    """Record every Engine.render call instead of performing it."""
    recorded: list[dict[str, object]] = []

    async def fake_render(
        self: Engine,
        display_id: str,
        trigger: str = "manual",
        force: bool = False,
        deliver: bool = True,
    ) -> RenderOutcome:
        recorded.append(
            {"display_id": display_id, "trigger": trigger, "force": force, "deliver": deliver}
        )
        return RenderOutcome(display_id=display_id, ok=True, trigger=trigger)

    monkeypatch.setattr(Engine, "render", fake_render)
    return recorded


@pytest.fixture
def client(config: Config):
    config.server.api_token = TOKEN
    # No `with`: the lifespan would start the engine, and with it a browser.
    return TestClient(create_app(Application(config)))


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_render_all_forwards_force_to_every_display(client: TestClient, calls) -> None:
    response = client.post("/api/render?force=true", headers=AUTH)

    assert response.status_code == 200
    assert {c["display_id"] for c in calls} == {"kitchen", "hallway"}
    assert calls, "no display was rendered"
    assert all(c["force"] is True for c in calls), calls
    assert all(c["trigger"] == "api" for c in calls)


def test_render_all_defaults_to_not_forcing(client: TestClient, calls) -> None:
    assert client.post("/api/render", headers=AUTH).status_code == 200
    assert calls, "no display was rendered"
    assert all(c["force"] is False for c in calls), calls


def test_single_display_render_forwards_force(client: TestClient, calls) -> None:
    assert client.post("/api/displays/kitchen/render?force=true", headers=AUTH).status_code == 200
    assert calls == [
        {"display_id": "kitchen", "trigger": "api", "force": True, "deliver": True}
    ]


async def test_engine_render_all_passes_force_through(config: Config, calls) -> None:
    """Guard the layer under the API too, so a direct caller is not surprised."""
    engine = Engine(config)

    await engine.render_all(trigger="schedule", force=True)

    assert {c["display_id"] for c in calls} == {"kitchen", "hallway"}
    assert all(c["force"] is True for c in calls)
    assert all(c["trigger"] == "schedule" for c in calls)


async def test_application_render_all_passes_force_through(config: Config, calls) -> None:
    application = Application(config)

    await application.render_all(force=True)

    assert calls, "no display was rendered"
    assert all(c["force"] is True for c in calls)
    assert all(c["trigger"] == "api" for c in calls)


async def test_disabled_displays_are_not_rendered(config: Config, calls) -> None:
    config.display("hallway").enabled = False

    await Engine(config).render_all(force=True)

    assert [c["display_id"] for c in calls] == ["kitchen"]
