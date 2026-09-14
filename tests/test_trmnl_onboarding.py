"""A TRMNL panel must be able to fetch its frame when server.api_token is set.

``/api/setup`` and ``/api/display`` hand the firmware an ``image_url`` pointing
at ``/api/displays/{id}/frame``, which sits behind ``_require_token``. The
firmware does not use ``Authorization: Bearer``; it sends its key in the
``Access-Token`` header, and it attaches that header to the image fetch too
when the image is hosted on the same server as the API — which is exactly
Maverick's case. So the frame endpoint has to accept that header.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from maverick.app import Application
from maverick.config import Config
from maverick.engine import StoredFrame
from maverick.server.api import create_app

MAC = "AA:BB:CC:DD:EE:FF"
TOKEN = "s3cret-token"


def _store_frame(application: Application, display_id: str) -> StoredFrame:
    """Put a frame in the store without running a browser.

    FrameStore.put() needs a real rendered Frame; the pull endpoint only needs
    the stored form, so that is what the test provides.
    """
    stored = StoredFrame(
        display_id=display_id,
        payload=b"\x00\xff" * 64,
        preview_png=b"",
        checksum="deadbeef",
        width=800,
        height=480,
        colors=2,
        frame_format="bwr_packed",
        media_type="application/octet-stream",
        rendered_at="2026-01-01T00:00:00+00:00",
    )
    application.engine.frames._frames[display_id] = stored
    return stored


@pytest.fixture
def client(config: Config):
    config.server.api_token = TOKEN
    application = Application(config)
    _store_frame(application, "kitchen")
    # No `with`: the lifespan would start the engine, and with it a browser.
    return TestClient(create_app(application))


def _fetch_as_firmware(client: TestClient, image_url: str, **headers: str):
    """GET image_url the way TRMNL firmware does for a same-host image."""
    path = urlsplit(image_url).path
    return client.get(path, headers={"ID": MAC, "Access-Token": TOKEN, **headers})


def test_setup_then_fetch_frame(client: TestClient) -> None:
    setup = client.get("/api/setup", headers={"ID": MAC})
    assert setup.status_code == 200
    body = setup.json()
    assert body["api_key"] == TOKEN
    assert body["image_url"].endswith("/api/displays/kitchen/frame")


def test_display_handshake_then_frame_and_304(client: TestClient) -> None:
    handshake = client.get("/api/display", headers={"ID": MAC})
    assert handshake.status_code == 200
    image_url = handshake.json()["image_url"]

    first = _fetch_as_firmware(client, image_url)
    assert first.status_code == 200, first.text
    assert first.content == b"\x00\xff" * 64
    etag = first.headers["ETag"]

    cached = _fetch_as_firmware(client, image_url, **{"If-None-Match": etag})
    assert cached.status_code == 304
    assert cached.headers["ETag"] == etag


def test_underscored_header_spelling_is_accepted(client: TestClient) -> None:
    """The BYOS reference docs spell the field ACCESS_TOKEN."""
    response = client.get(
        "/api/displays/kitchen/frame", headers={"ID": MAC, "ACCESS_TOKEN": TOKEN}
    )
    assert response.status_code == 200


def test_bearer_and_query_token_still_work(client: TestClient) -> None:
    assert (
        client.get(
            "/api/displays/kitchen/frame", headers={"Authorization": f"Bearer {TOKEN}"}
        ).status_code
        == 200
    )
    assert client.get(f"/api/displays/kitchen/frame?token={TOKEN}").status_code == 200


def test_frame_is_still_refused_without_a_token(client: TestClient) -> None:
    assert client.get("/api/displays/kitchen/frame").status_code == 401
    assert client.get("/api/displays/kitchen/frame", headers={"ID": MAC}).status_code == 401


def test_a_wrong_access_token_is_refused(client: TestClient) -> None:
    for bad in ("", "nope", TOKEN + "x", TOKEN[:-1]):
        response = client.get(
            "/api/displays/kitchen/frame", headers={"Access-Token": bad}
        )
        assert response.status_code == 401, f"{bad!r} must not authenticate"


def test_unknown_mac_gets_no_display(client: TestClient) -> None:
    assert client.get("/api/setup", headers={"ID": "00:00:00:00:00:00"}).status_code == 404
    assert client.get("/api/display", headers={"ID": "00:00:00:00:00:00"}).status_code == 404
