"""`HomeAssistantClient.list_dashboards` and the `GET /api/ha/dashboards` route.

The fixture payloads below are shaped like Home Assistant 2024.10's WebSocket
API: `lovelace/dashboards/list` returns each *extra* dashboard's own fields
(`id`, `url_path`, `title`, `mode`, ...) and `lovelace/config` returns that
dashboard's configuration, `views` included. They are modelled from the
source (`homeassistant/components/lovelace/dashboard.py`,
`homeassistant/components/lovelace/websocket.py`), not captured from a live
instance — the suite runs with no Home Assistant to capture from
(`tests/conftest.py`) — so `_ws_call` is monkeypatched to hand them back
directly rather than opening a real WebSocket.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from conftest import DISPLAY_ID, FakeRenderer
from fastapi.testclient import TestClient

from maverick import engine as engine_module
from maverick.app import Application
from maverick.config import Config, HomeAssistantConfig
from maverick.ha.client import HomeAssistantClient, HomeAssistantError
from maverick.server.api import create_app

# --------------------------------------------------------------------------- #
# Fixture payloads
# --------------------------------------------------------------------------- #

#: `lovelace/dashboards/list` never includes the built-in default dashboard
#: (see the docstring of `HomeAssistantClient.list_dashboards`), only the
#: *extra* ones: one in storage mode, one in YAML mode.
DASHBOARDS_LIST_RESPONSE: list[dict[str, Any]] = [
    {
        "id": "01j8x8f6z3q5r7t9v1w2y4z6a8",
        "url_path": "admin-dash",
        "title": "Admin",
        "mode": "storage",
        "icon": "mdi:shield-account",
        "show_in_sidebar": True,
        "require_admin": True,
    },
    {
        "id": "01j8x8f6z3q5r7t9v1w2y4z6a9",
        "url_path": "yaml-dash",
        "title": "YAML dashboard",
        "mode": "yaml",
        "icon": None,
        "show_in_sidebar": True,
        "require_admin": False,
    },
]

#: `lovelace/config` for the default dashboard (no `url_path` in the request).
DEFAULT_DASHBOARD_CONFIG: dict[str, Any] = {
    "title": "Home",
    "views": [
        {"path": "default_view", "title": "Home"},
        {"title": "Kitchen"},  # no explicit path: falls back to its index
    ],
}

#: `lovelace/config` for the storage-mode extra dashboard.
ADMIN_DASHBOARD_CONFIG: dict[str, Any] = {
    "views": [{"path": "overview", "title": "Overview"}],
}


async def _fake_ws_call(self: HomeAssistantClient, message_type: str, **payload: Any) -> Any:
    del self
    if message_type == "lovelace/dashboards/list":
        return DASHBOARDS_LIST_RESPONSE
    if message_type == "lovelace/config":
        url_path = payload.get("url_path")
        if url_path is None:
            return DEFAULT_DASHBOARD_CONFIG
        if url_path == "admin-dash":
            return ADMIN_DASHBOARD_CONFIG
        if url_path == "yaml-dash":
            # A YAML-mode dashboard's config cannot be read this way.
            raise HomeAssistantError("lovelace/config failed: dashboard is in YAML mode")
        raise AssertionError(f"unexpected url_path {url_path!r}")
    raise AssertionError(f"unexpected message type {message_type!r}")


@pytest.fixture
def client() -> HomeAssistantClient:
    return HomeAssistantClient(HomeAssistantConfig(token="test-token"))


# --------------------------------------------------------------------------- #
# HomeAssistantClient.list_dashboards
# --------------------------------------------------------------------------- #


async def test_list_dashboards_shape_and_default_dashboard(
    monkeypatch: pytest.MonkeyPatch, client: HomeAssistantClient
) -> None:
    monkeypatch.setattr(HomeAssistantClient, "_ws_call", _fake_ws_call)

    dashboards = await client.list_dashboards()

    by_path = {d["url_path"]: d for d in dashboards}
    assert set(by_path) == {"lovelace", "admin-dash", "yaml-dash"}
    for dashboard in dashboards:
        assert set(dashboard) == {"url_path", "title", "views"}
        for view in dashboard["views"]:
            assert set(view) == {"path", "title"}


async def test_view_paths_are_built_from_url_path_and_view_path_or_index(
    monkeypatch: pytest.MonkeyPatch, client: HomeAssistantClient
) -> None:
    monkeypatch.setattr(HomeAssistantClient, "_ws_call", _fake_ws_call)

    dashboards = await client.list_dashboards()
    by_path = {d["url_path"]: d for d in dashboards}

    default = by_path["lovelace"]
    assert default["title"] == "Home"
    assert default["views"] == [
        {"path": "/lovelace/default_view", "title": "Home"},
        {"path": "/lovelace/1", "title": "Kitchen"},
    ]

    admin = by_path["admin-dash"]
    assert admin["title"] == "Admin"
    assert admin["views"] == [{"path": "/admin-dash/overview", "title": "Overview"}]


async def test_a_dashboard_whose_config_cannot_be_read_keeps_empty_views(
    monkeypatch: pytest.MonkeyPatch, client: HomeAssistantClient
) -> None:
    monkeypatch.setattr(HomeAssistantClient, "_ws_call", _fake_ws_call)

    dashboards = await client.list_dashboards()
    by_path = {d["url_path"]: d for d in dashboards}

    yaml_dash = by_path["yaml-dash"]
    assert yaml_dash["title"] == "YAML dashboard"
    assert yaml_dash["views"] == []


async def test_listing_extra_dashboards_failing_raises_rather_than_falling_back(
    monkeypatch: pytest.MonkeyPatch, client: HomeAssistantClient
) -> None:
    """Unlike a single dashboard's `lovelace/config` (caught, empty `views`),
    `lovelace/dashboards/list` failing means there is nothing to list at all,
    so it propagates — this is what lets `GET /api/ha/dashboards` answer 503
    with a message rather than silently showing only the default dashboard."""

    async def failing_ws_call(self: HomeAssistantClient, message_type: str, **payload: Any) -> Any:
        del self, payload
        if message_type == "lovelace/dashboards/list":
            raise HomeAssistantError("lovelace/dashboards/list failed: not permitted")
        raise AssertionError(f"unexpected message type {message_type!r}")

    monkeypatch.setattr(HomeAssistantClient, "_ws_call", failing_ws_call)

    with pytest.raises(HomeAssistantError, match="not permitted"):
        await client.list_dashboards()


# --------------------------------------------------------------------------- #
# GET /api/ha/dashboards
# --------------------------------------------------------------------------- #


class _FakeHaClient:
    """Just enough of `HomeAssistantClient` for the route: one method."""

    def __init__(
        self, dashboards: list[dict[str, Any]] | None = None, error: Exception | None = None
    ):
        self._dashboards = dashboards or []
        self._error = error

    async def list_dashboards(self) -> list[dict[str, Any]]:
        if self._error is not None:
            raise self._error
        return self._dashboards

    async def close(self) -> None:
        """`Engine.stop()` calls this on whatever `engine._ha` holds."""


@pytest.fixture
def api_harness(minimal_config: Config, monkeypatch: pytest.MonkeyPatch, legible_image):
    """A started `Application`, reached over HTTP — same shape as
    tests/test_display_api.py's `harness`, kept local since it is the only
    thing this module needs from it."""
    minimal_config.display(DISPLAY_ID).schedule.render_on_start = False
    renderer = FakeRenderer(legible_image)
    monkeypatch.setattr(
        engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer
    )
    app = Application(minimal_config)
    with TestClient(create_app(app)) as client:
        yield SimpleNamespace(client=client, app=app)


def test_dashboards_route_503s_when_not_connected(api_harness) -> None:
    """`minimal_config` sets no Home Assistant credential, so `engine.ha_ok`
    is false without any monkeypatching — the state a fresh install is in."""
    response = api_harness.client.get("/api/ha/dashboards")

    assert response.status_code == 503
    assert "Home Assistant" in response.json()["detail"]


def test_dashboards_route_returns_the_list_when_connected(api_harness) -> None:
    payload = [{"url_path": "lovelace", "title": "Overview", "views": []}]
    api_harness.app.engine._ha = _FakeHaClient(payload)
    api_harness.app.engine._ha_ok = True

    response = api_harness.client.get("/api/ha/dashboards")

    assert response.status_code == 200
    assert response.json() == payload


def test_dashboards_route_503s_when_the_call_itself_fails(api_harness) -> None:
    api_harness.app.engine._ha = _FakeHaClient(error=HomeAssistantError("boom"))
    api_harness.app.engine._ha_ok = True

    response = api_harness.client.get("/api/ha/dashboards")

    assert response.status_code == 503
    assert "boom" in response.json()["detail"]
