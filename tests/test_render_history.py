"""Render history: what `DisplayState` forgets once the next render succeeds.

`DisplayState.last_error` and `consecutive_failures` (`src/maverick/engine.py`)
are the *last* outcome only — a render that fails once in ten leaves no trace
once the eleventh succeeds, and the MQTT state topic publishes one snapshot at
a time for the same reason. `Engine._notify`, which every path through
`Engine.render` calls — success, failure, a lint block, an unchanged skip —
appends each outcome to a bounded, persisted history instead, and
`GET /api/displays/{id}/history` serves it.

Built on the same `make_engine`/`minimal_config` doubles as
tests/test_runtime_displays.py and the same HTTP harness as
tests/test_screenshot_route.py: nothing here opens a browser, a broker or a
socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import DISPLAY_ID, FakeRenderer
from fastapi.testclient import TestClient

from maverick import engine as engine_module
from maverick.app import Application
from maverick.config import Config
from maverick.engine import HISTORY_LIMIT, Engine
from maverick.render.dashboard import RenderError
from maverick.server.api import create_app


class FailingRenderer:
    """Stands in for `DashboardRenderer` when a render must fail outright."""

    async def render(self, display):  # type: ignore[no-untyped-def]
        raise RenderError("dashboard did not load")


def _patch_renderer(monkeypatch, renderer) -> None:
    monkeypatch.setattr(engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer)


# --------------------------------------------------------------------------- #
# Engine.render appends one row per outcome
# --------------------------------------------------------------------------- #


async def test_a_success_produces_an_ok_row(make_engine, legible_image) -> None:
    harness = await make_engine(legible_image)

    await harness.engine.render(DISPLAY_ID, trigger="schedule")

    rows = harness.engine.render_history(DISPLAY_ID)
    assert len(rows) == 1
    row = rows[0]
    assert row["ok"] is True
    assert row["skipped"] is False
    assert row["trigger"] == "schedule"
    assert row["reason"] == ""
    assert row["checksum"]
    assert row["lint_summary"]
    assert row["delivery"] == "recorded"
    assert row["total_s"] >= 0
    assert row["at"]


async def test_a_lint_block_produces_a_blocked_row(make_engine, blank_image) -> None:
    harness = await make_engine(blank_image)

    await harness.engine.render(DISPLAY_ID, trigger="manual")

    row = harness.engine.render_history(DISPLAY_ID)[0]
    assert row["ok"] is False
    assert row["skipped"] is True
    assert row["reason"].startswith("blocked by lint")
    assert row["lint_summary"]


async def test_an_unchanged_render_produces_a_skipped_row(make_engine, legible_image) -> None:
    harness = await make_engine(legible_image)
    await harness.engine.render(DISPLAY_ID, trigger="schedule")

    await harness.engine.render(DISPLAY_ID, trigger="schedule")

    rows = harness.engine.render_history(DISPLAY_ID)
    assert len(rows) == 2
    newest = rows[0]  # newest first
    assert newest["ok"] is True
    assert newest["skipped"] is True
    assert newest["reason"] == "frame unchanged"


async def test_a_render_failure_produces_a_failed_row(minimal_config: Config, monkeypatch) -> None:
    _patch_renderer(monkeypatch, FailingRenderer())
    engine = Engine(minimal_config)
    await engine.start()

    await engine.render(DISPLAY_ID, trigger="manual")

    row = engine.render_history(DISPLAY_ID)[0]
    assert row["ok"] is False
    assert row["skipped"] is False
    assert "dashboard did not load" in row["reason"]
    assert row["checksum"] == "", "a render that never produced a frame has no checksum"


async def test_render_candidate_never_touches_history(
    make_engine, legible_image, minimal_config
) -> None:
    """A dry-run preview leaves no trace: `render_candidate` never calls `_notify`."""
    harness = await make_engine(legible_image)
    candidate = minimal_config.display(DISPLAY_ID).model_copy()

    await harness.engine.render_candidate(candidate)

    assert harness.engine.render_history(DISPLAY_ID) == []


# --------------------------------------------------------------------------- #
# The buffer is bounded
# --------------------------------------------------------------------------- #


async def test_the_buffer_is_bounded_at_fifty(make_engine, legible_image) -> None:
    harness = await make_engine(legible_image)

    for _ in range(HISTORY_LIMIT + 5):
        await harness.engine.render(DISPLAY_ID, trigger="manual")

    assert len(harness.engine.history[DISPLAY_ID]) == HISTORY_LIMIT
    assert len(harness.engine.render_history(DISPLAY_ID, limit=HISTORY_LIMIT)) == HISTORY_LIMIT


async def test_render_history_respects_its_own_limit(make_engine, legible_image) -> None:
    harness = await make_engine(legible_image)
    for _ in range(5):
        await harness.engine.render(DISPLAY_ID, trigger="manual")

    assert len(harness.engine.render_history(DISPLAY_ID, limit=3)) == 3


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


async def test_history_round_trips_through_a_new_engine_on_the_same_data_dir(
    minimal_config: Config, monkeypatch, legible_image
) -> None:
    _patch_renderer(monkeypatch, FakeRenderer(legible_image))
    first = Engine(minimal_config)
    await first.start()
    await first.render(DISPLAY_ID, trigger="schedule")
    await first.render(DISPLAY_ID, trigger="manual", force=True)

    second = Engine(minimal_config)
    await second.start()

    rows = second.render_history(DISPLAY_ID)
    assert [row["trigger"] for row in rows] == ["manual", "schedule"]


async def test_history_write_then_rename_leaves_no_tmp_file_behind(
    make_engine, legible_image
) -> None:
    harness = await make_engine(legible_image)

    await harness.engine.render(DISPLAY_ID, trigger="manual")

    directory = harness.engine.data_dir / "history"
    assert (directory / f"{DISPLAY_ID}.json").exists()
    assert not list(directory.glob("*.tmp")), "write-then-rename should leave none"


async def test_an_unreadable_history_file_is_ignored_not_fatal(
    minimal_config: Config, monkeypatch, legible_image, caplog
) -> None:
    _patch_renderer(monkeypatch, FakeRenderer(legible_image))
    directory = Path(minimal_config.data_dir) / "history"
    directory.mkdir(parents=True)
    (directory / f"{DISPLAY_ID}.json").write_text("not json")

    engine = Engine(minimal_config)
    with caplog.at_level("WARNING"):
        await engine.start()

    assert engine.render_history(DISPLAY_ID) == []
    assert "ignoring unreadable history file" in caplog.text


# --------------------------------------------------------------------------- #
# Removal deletes the file
# --------------------------------------------------------------------------- #


async def test_unregister_display_deletes_the_history_file(make_engine, legible_image) -> None:
    harness = await make_engine(legible_image)
    await harness.engine.render(DISPLAY_ID, trigger="manual")
    path = harness.engine.data_dir / "history" / f"{DISPLAY_ID}.json"
    assert path.exists()

    await harness.engine.unregister_display(DISPLAY_ID)

    assert not path.exists()
    assert harness.engine.render_history(DISPLAY_ID) == []


# --------------------------------------------------------------------------- #
# GET /api/displays/{id}/history
# --------------------------------------------------------------------------- #


@dataclass
class ApiHarness:
    client: TestClient
    app: Application


@pytest.fixture
def harness(minimal_config: Config, monkeypatch, legible_image):
    minimal_config.display(DISPLAY_ID).schedule.render_on_start = False
    _patch_renderer(monkeypatch, FakeRenderer(legible_image))
    app = Application(minimal_config)
    with TestClient(create_app(app)) as client:
        yield ApiHarness(client=client, app=app)


def test_history_route_is_empty_before_any_render(harness: ApiHarness) -> None:
    response = harness.client.get(f"/api/displays/{DISPLAY_ID}/history")

    assert response.status_code == 200
    assert response.json() == []


def test_history_route_returns_newest_first(harness: ApiHarness) -> None:
    harness.client.post(f"/api/displays/{DISPLAY_ID}/render")
    harness.client.post(f"/api/displays/{DISPLAY_ID}/render?force=true")

    rows = harness.client.get(f"/api/displays/{DISPLAY_ID}/history").json()

    assert len(rows) == 2
    assert rows[0]["at"] >= rows[1]["at"]
    assert rows[0]["trigger"] == "api"


def test_history_route_honours_limit(harness: ApiHarness) -> None:
    for _ in range(3):
        harness.client.post(f"/api/displays/{DISPLAY_ID}/render?force=true")

    rows = harness.client.get(f"/api/displays/{DISPLAY_ID}/history?limit=2").json()

    assert len(rows) == 2


def test_history_route_defaults_to_twenty(harness: ApiHarness) -> None:
    for _ in range(25):
        harness.client.post(f"/api/displays/{DISPLAY_ID}/render?force=true")

    rows = harness.client.get(f"/api/displays/{DISPLAY_ID}/history").json()

    assert len(rows) == 20


def test_history_route_rejects_a_limit_over_the_maximum(harness: ApiHarness) -> None:
    response = harness.client.get(f"/api/displays/{DISPLAY_ID}/history?limit=51")

    assert response.status_code == 422


def test_history_route_404s_for_an_unknown_display(harness: ApiHarness) -> None:
    response = harness.client.get("/api/displays/nowhere/history")

    assert response.status_code == 404
