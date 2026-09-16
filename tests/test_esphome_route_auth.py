"""``/api/displays/{id}/esphome.yaml`` and ``/esphome`` must not leak the token.

The generated document references ``server.api_token`` as
``!secret maverick_authorization`` rather than embedding it
(`src/maverick/esphome/generator.py`), so the YAML itself is no longer a
secret. The JSON companion, ``GET /api/displays/{id}/esphome``, *is*: it
carries the secret's value so the setup UI can hand it to the user in one
step. Both routes therefore need the same ``_require_token`` dependency as
every other ``/api/displays/...`` route (`src/maverick/server/api.py`).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from maverick.app import Application
from maverick.config import Config
from maverick.server.api import create_app

_TOKEN = "s3cret-token"


async def _noop(*args, **kwargs) -> None:
    return None


def _config(tmp_path, *, token: str) -> Config:
    return Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "server": {"base_url": "http://192.168.1.10:5000", "api_token": token},
            "displays": [
                {"id": "kitchen", "panel": "waveshare-7in5-mono", "transport": {"type": "file"}}
            ],
        }
    )


def _app(config: Config, monkeypatch) -> Application:
    # The engine would otherwise start a browser pool and a scheduler.
    monkeypatch.setattr(Application, "start", _noop)
    monkeypatch.setattr(Application, "stop", _noop)
    return Application(config)


@pytest.fixture
def token_app(tmp_path, monkeypatch) -> Application:
    return _app(_config(tmp_path, token=_TOKEN), monkeypatch)


@pytest.fixture
def open_app(tmp_path, monkeypatch) -> Application:
    return _app(_config(tmp_path, token=""), monkeypatch)


@pytest.mark.parametrize("route", ["esphome.yaml", "esphome"])
def test_unauthenticated_request_is_rejected_and_the_token_is_not_leaked(
    token_app: Application, route: str
) -> None:
    with TestClient(create_app(token_app)) as client:
        response = client.get(f"/api/displays/kitchen/{route}")
    assert response.status_code == 401
    assert _TOKEN not in response.text


def test_the_yaml_references_the_token_as_a_secret(token_app: Application) -> None:
    """The document itself carries no secret: the token is a `!secret` name."""
    with TestClient(create_app(token_app)) as client:
        response = client.get(
            "/api/displays/kitchen/esphome.yaml",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    assert response.status_code == 200
    assert "Authorization: !secret maverick_authorization" in response.text
    assert _TOKEN not in response.text


def test_the_json_companion_carries_the_secret_value(token_app: Application) -> None:
    """The one place the value appears, so the UI can offer it for secrets.yaml."""
    with TestClient(create_app(token_app)) as client:
        response = client.get(
            "/api/displays/kitchen/esphome", headers={"Authorization": f"Bearer {_TOKEN}"}
        )
    assert response.status_code == 200
    body = response.json()
    secrets = {entry["name"]: entry["value"] for entry in body["secrets"]}
    assert secrets["maverick_authorization"] == f"Bearer {_TOKEN}"
    assert secrets["wifi_ssid"] is None, "Maverick does not know the Wi-Fi password"
    assert body["node"] == "kitchen-panel"
    assert body["filename"] == "kitchen-panel.yaml"
    assert body["model_known"] is True
    assert body["yaml"] == (
        TestClient(create_app(token_app))
        .get("/api/displays/kitchen/esphome.yaml", headers={"Authorization": f"Bearer {_TOKEN}"})
        .text
    )


def test_query_token_is_accepted(token_app: Application) -> None:
    """A plain anchor cannot send a header, so `?token=` still works."""
    with TestClient(create_app(token_app)) as client:
        response = client.get(f"/api/displays/kitchen/esphome.yaml?token={_TOKEN}")
    assert response.status_code == 200


def test_no_token_configured_leaves_the_route_open_and_asks_for_no_secret(
    open_app: Application,
) -> None:
    with TestClient(create_app(open_app)) as client:
        yaml = client.get("/api/displays/kitchen/esphome.yaml")
        body = client.get("/api/displays/kitchen/esphome").json()
    assert yaml.status_code == 200
    assert "request_headers" not in yaml.text
    assert [entry["name"] for entry in body["secrets"]] == [
        "wifi_ssid", "wifi_password", "api_key",
    ]
