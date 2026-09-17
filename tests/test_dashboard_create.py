"""One click from "added a display" to "a dashboard on the panel".

`POST /api/displays/{id}/dashboard/create` (`src/maverick/server/api.py`)
creates the starter as a storage-mode dashboard in Home Assistant through
`lovelace/dashboards/create` and `lovelace/config/save`
(`HomeAssistantClient.create_dashboard` and `.save_dashboard_config`,
`src/maverick/ha/client.py`), then points the display at its view. The
WebSocket is a double here, as in `tests/test_ha_dashboards.py`: the command
names and field names are Home Assistant's, read from
`homeassistant/components/lovelace` (`const.py` for the create fields,
`websocket.py` for the save), not captured from a live instance.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from conftest import DISPLAY_ID, FakeRenderer
from fastapi.testclient import TestClient

from maverick import engine as engine_module
from maverick.app import Application
from maverick.config import Config, DisplayConfig, HomeAssistantConfig
from maverick.ha.client import HomeAssistantClient, HomeAssistantError
from maverick.lovelace import (
    dashboard_url_path,
    generate_dashboard,
    generate_dashboard_config,
    starter_view_path,
)
from maverick.server.api import create_app


class FakeWebSocket:
    """Records the commands the client sends and plays a registry of dashboards."""

    def install(self, monkeypatch) -> None:
        """Stand in for `HomeAssistantClient._ws_call` (a method, so bound)."""
        fake = self

        async def _ws_call(client: HomeAssistantClient, message_type: str, **payload: Any):
            return await fake(client, message_type, **payload)

        monkeypatch.setattr(HomeAssistantClient, "_ws_call", _ws_call)

    def __init__(self, existing: set[str] | None = None, fail: str | None = None) -> None:
        self.existing = set(existing or ())
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.saved: dict[str, dict[str, Any]] = {}

    async def __call__(self, client: HomeAssistantClient, message_type: str, **payload: Any):
        del client
        self.calls.append((message_type, payload))
        if self.fail == message_type:
            raise HomeAssistantError(
                f"Home Assistant WebSocket command {message_type!r} failed: Unauthorized"
            )
        if message_type == "lovelace/dashboards/list":
            return [{"url_path": path, "title": path} for path in sorted(self.existing)]
        if message_type == "lovelace/dashboards/create":
            assert payload["url_path"] not in self.existing
            assert "-" in payload["url_path"], "Home Assistant requires a hyphen"
            assert payload["title"]
            self.existing.add(payload["url_path"])
            return {"id": "abc", **payload}
        if message_type == "lovelace/config/save":
            assert payload["url_path"] in self.existing
            self.saved[payload["url_path"]] = payload["config"]
            return None
        raise AssertionError(f"unexpected message type {message_type!r}")


# --------------------------------------------------------------------------- #
# the generator
# --------------------------------------------------------------------------- #


def test_the_config_mapping_is_what_the_yaml_carries() -> None:
    import yaml

    display = DisplayConfig.model_validate(
        {"id": "kitchen_wall", "name": "Kitchen", "panel": "waveshare-7in5-mono"}
    ).resolved()
    config = generate_dashboard_config(display, None)
    assert yaml.safe_load(generate_dashboard(display, None)) == config
    assert dashboard_url_path(display) == "maverick-kitchen-wall", "a hyphen, and no underscores"
    assert starter_view_path(display) == "/maverick-kitchen-wall/eink-kitchen_wall"
    assert config["views"][0]["path"] == "eink-kitchen_wall"


# --------------------------------------------------------------------------- #
# the client
# --------------------------------------------------------------------------- #


async def test_create_and_save_send_home_assistants_own_commands(monkeypatch) -> None:
    ws = FakeWebSocket()
    ws.install(monkeypatch)
    client = HomeAssistantClient(HomeAssistantConfig(url="http://ha.local:8123", token="t"))
    assert await client.dashboard_url_paths() == set()
    await client.create_dashboard("maverick-kitchen", "Kitchen")
    await client.save_dashboard_config("maverick-kitchen", {"views": []})
    assert [name for name, _ in ws.calls] == [
        "lovelace/dashboards/list", "lovelace/dashboards/create", "lovelace/config/save",
    ]
    create = ws.calls[1][1]
    assert create == {
        "url_path": "maverick-kitchen", "title": "Kitchen", "icon": "mdi:tablet-dashboard",
        "show_in_sidebar": True, "require_admin": False,
    }
    assert ws.saved["maverick-kitchen"] == {"views": []}


# --------------------------------------------------------------------------- #
# the route
# --------------------------------------------------------------------------- #


@pytest.fixture
def served(minimal_config: Config, monkeypatch, legible_image):
    minimal_config.display(DISPLAY_ID).schedule.render_on_start = False
    minimal_config.home_assistant.url = "http://ha.local:8123"
    minimal_config.displays.append(
        DisplayConfig.model_validate(
            {
                "id": "paged",
                "panel": "generic-mono",
                "pages": [{"dashboard": "/lovelace/0"}, {"dashboard": "/lovelace/1"}],
                "transport": {"type": "fake"},
                "schedule": {"render_on_start": False},
            }
        )
    )
    renderer = FakeRenderer(legible_image)
    monkeypatch.setattr(
        engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer
    )
    app = Application(minimal_config)
    with TestClient(create_app(app)) as client:
        ws = FakeWebSocket()
        ws.install(monkeypatch)
        fake = HomeAssistantClient(HomeAssistantConfig(url="http://ha.local:8123", token="t"))

        async def no_states() -> list[dict[str, Any]]:
            return []

        fake.list_states = no_states  # type: ignore[method-assign]
        app.engine._ha = fake
        app.engine._ha_ok = True
        yield SimpleNamespace(client=client, app=app, ws=ws)


def test_create_makes_the_dashboard_and_points_the_display_at_it(served) -> None:
    response = served.client.post(f"/api/displays/{DISPLAY_ID}/dashboard/create", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] is True and body["replaced"] is False and body["applied"] is True
    assert body["url_path"] == f"maverick-{DISPLAY_ID}"
    assert body["path"] == f"/maverick-{DISPLAY_ID}/eink-{DISPLAY_ID}"
    assert body["open_url"] == f"http://ha.local:8123{body['path']}"
    assert body["edit_url"] == f"http://ha.local:8123{body['path']}?edit=1"
    assert served.ws.saved[body["url_path"]]["views"][0]["cards"], "the starter was saved"
    assert served.app.config.display(DISPLAY_ID).dashboard == body["path"]
    summary = served.client.get(f"/api/displays/{DISPLAY_ID}").json()
    assert summary["dashboard"] == body["path"]
    assert summary["edit_url"] == body["edit_url"]


def test_an_existing_dashboard_is_the_users_until_overwrite_says_otherwise(served) -> None:
    served.ws.existing.add(f"maverick-{DISPLAY_ID}")
    refused = served.client.post(f"/api/displays/{DISPLAY_ID}/dashboard/create", json={})
    assert refused.status_code == 409
    assert "already has a dashboard" in refused.json()["detail"]
    assert not served.ws.saved, "nothing was written"

    replaced = served.client.post(
        f"/api/displays/{DISPLAY_ID}/dashboard/create", json={"overwrite": True}
    )
    assert replaced.status_code == 200
    assert replaced.json()["replaced"] is True and replaced.json()["created"] is False
    assert "lovelace/dashboards/create" not in [name for name, _ in served.ws.calls]
    assert f"maverick-{DISPLAY_ID}" in served.ws.saved


def test_a_display_with_pages_keeps_them(served) -> None:
    response = served.client.post("/api/displays/paged/dashboard/create", json={})
    assert response.status_code == 200
    assert response.json()["applied"] is False
    assert served.app.config.display("paged").pages, "pages survived"


def test_home_assistants_refusal_is_a_503_and_nothing_is_half_done(served, monkeypatch) -> None:
    served.ws.fail = "lovelace/dashboards/create"
    response = served.client.post(f"/api/displays/{DISPLAY_ID}/dashboard/create", json={})
    assert response.status_code == 503
    assert "Unauthorized" in response.json()["detail"]
    assert not served.ws.saved
    starter = f"/maverick-{DISPLAY_ID}/eink-{DISPLAY_ID}"
    assert served.app.config.display(DISPLAY_ID).dashboard != starter


def test_without_home_assistant_the_route_says_so(served) -> None:
    served.app.engine._ha_ok = False
    response = served.client.post(f"/api/displays/{DISPLAY_ID}/dashboard/create", json={})
    assert response.status_code == 503


# --------------------------------------------------------------------------- #
# the edit link
# --------------------------------------------------------------------------- #


def test_the_summary_carries_the_editor_url_for_a_home_assistant_page(served) -> None:
    summary = served.client.get(f"/api/displays/{DISPLAY_ID}").json()
    assert summary["dashboard_url"] == "http://ha.local:8123/lovelace/0"
    assert summary["edit_url"] == "http://ha.local:8123/lovelace/0?edit=1"


def test_a_page_that_is_not_home_assistants_has_no_editor(served) -> None:
    display = served.app.config.display(DISPLAY_ID)
    display.dashboard = "file:///srv/board.html"
    summary = served.client.get(f"/api/displays/{DISPLAY_ID}").json()
    assert summary["dashboard_url"] is None and summary["edit_url"] is None


def test_lint_findings_carry_plain_advice(served) -> None:
    from maverick.server.copy import lint_advice

    assert lint_advice("hairlines").startswith("Lines and letters are too thin")
    assert lint_advice("spot_ink_overuse.red").startswith("Too much colour ink")
    assert lint_advice("something_new") == ""
