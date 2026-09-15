"""``GET /api/displays/{id}/esphome.yaml`` must not leak ``server.api_token``.

The generated document embeds the token verbatim as an ``Authorization:
Bearer`` header (`src/maverick/esphome/generator.py`), so the route that
serves it needs the same ``_require_token`` dependency as every other
``/api/displays/...`` route (`src/maverick/server/api.py`) — otherwise an
unauthenticated request on the published port hands back the secret that
gates the rest of the API.
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
                {"id": "kitchen", "panel": "trmnl-7in5", "transport": {"type": "file"}}
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


def test_unauthenticated_request_is_rejected_and_the_token_is_not_leaked(
    token_app: Application,
) -> None:
    with TestClient(create_app(token_app)) as client:
        response = client.get("/api/displays/kitchen/esphome.yaml")
    assert response.status_code == 401
    assert _TOKEN not in response.text


def test_bearer_header_is_accepted(token_app: Application) -> None:
    with TestClient(create_app(token_app)) as client:
        response = client.get(
            "/api/displays/kitchen/esphome.yaml",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    assert response.status_code == 200
    assert f'Authorization: "Bearer {_TOKEN}"' in response.text


def test_query_token_is_accepted(token_app: Application) -> None:
    """The setup UI's plain anchor cannot send a header, so it uses ``?token=``."""
    with TestClient(create_app(token_app)) as client:
        response = client.get(f"/api/displays/kitchen/esphome.yaml?token={_TOKEN}")
    assert response.status_code == 200
    assert f'Authorization: "Bearer {_TOKEN}"' in response.text


def test_no_token_configured_leaves_the_route_open(open_app: Application) -> None:
    with TestClient(create_app(open_app)) as client:
        response = client.get("/api/displays/kitchen/esphome.yaml")
    assert response.status_code == 200
