"""What ``server.api_token`` protects on the published port, and what ingress skips.

The setup UI at ``/`` and ``preview.png`` used to be open whatever
``server.api_token`` said, which on the port the app publishes
(``app/config.yaml``, ``ports``) meant anyone on the LAN could open the page
and read every panel off it. Both are behind the token now
(`src/maverick/server/api.py`).

The exemption is the interesting half. Home Assistant's ingress proxies to the
app with its own login already in front and no token of ours to send, so a
request from the Supervisor's ingress address is let through — but only while
Maverick is actually running as an app, because on any other network that
address is just an address somebody could hold
(`request_is_from_ingress` in `src/maverick/ha/supervisor.py`). Starlette's
``TestClient`` takes a ``client=(host, port)`` argument, which is how the peer
address is simulated below.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from maverick.app import Application
from maverick.config import Config
from maverick.eink.pipeline import PipelineOptions, process
from maverick.ha import supervisor
from maverick.server.api import create_app

_TOKEN = "s3cret-token"

#: Any address that is not the ingress proxy: a browser on the LAN.
_LAN = ("192.168.1.50", 41000)
_INGRESS = (supervisor.INGRESS_PEER, 41000)


async def _noop(*args, **kwargs) -> None:
    return None


def _config(tmp_path, *, token: str) -> Config:
    return Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "server": {"base_url": "http://192.168.1.10:5000", "api_token": token},
            "displays": [
                {
                    "id": "kitchen",
                    "panel": "generic-mono",
                    "width": 160,
                    "height": 120,
                    "transport": {"type": "file"},
                }
            ],
        }
    )


def _app(config: Config, monkeypatch) -> Application:
    # The engine would otherwise start a browser pool and a scheduler.
    monkeypatch.setattr(Application, "start", _noop)
    monkeypatch.setattr(Application, "stop", _noop)
    application = Application(config)
    _stage_a_frame(application)
    return application


def _stage_a_frame(application: Application) -> None:
    """Give the store a real frame, so ``preview.png`` can answer 200.

    Built through the pipeline rather than faked: the route serves
    ``StoredFrame.preview_png``, which only exists because ``FrameStore.put``
    encoded ``Frame.preview`` (`src/maverick/engine.py`).
    """
    resolved = application.config.display("kitchen").resolved()
    image = Image.new("RGB", (resolved.width, resolved.height), "white")
    image.paste(Image.new("RGB", (60, 40), "black"), (10, 10))
    frame = process(image, PipelineOptions(width=resolved.width, height=resolved.height))
    application.engine.frames.put("kitchen", frame)


@pytest.fixture(autouse=True)
def not_an_app(monkeypatch):
    """The default for every test here: a standalone ``maverick serve``.

    The suite must not inherit a ``SUPERVISOR_TOKEN`` from whatever shell runs
    it, or the ingress exemption would be live in tests that are about the
    published port.
    """
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)


@pytest.fixture
def token_app(tmp_path, monkeypatch) -> Application:
    return _app(_config(tmp_path, token=_TOKEN), monkeypatch)


@pytest.fixture
def open_app(tmp_path, monkeypatch) -> Application:
    return _app(_config(tmp_path, token=""), monkeypatch)


def _client(application: Application, peer: tuple[str, int]) -> TestClient:
    return TestClient(create_app(application), client=peer)


# --------------------------------------------------------------------------- #
# The published port, with a token set
# --------------------------------------------------------------------------- #

def test_the_ui_is_closed_to_the_lan(token_app: Application) -> None:
    with _client(token_app, _LAN) as client:
        response = client.get("/")
    assert response.status_code == 401
    assert _TOKEN not in response.text


def test_the_closed_ui_asks_for_the_token_rather_than_returning_json(
    token_app: Application,
) -> None:
    """A person is the only audience for ``/``, and a browser cannot send a header.

    The dependency's ``{"detail": ...}`` would leave them with nothing to do,
    which is how the token became a thing nobody set.
    """
    with _client(token_app, _LAN) as client:
        response = client.get("/")
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="token-field"' in response.text


def test_a_rejected_token_says_so_instead_of_asking_again(token_app: Application) -> None:
    """Otherwise the page would offer the stored token back and loop."""
    with _client(token_app, _LAN) as client:
        response = client.get("/?token=wrong")
    assert response.status_code == 401
    assert "not accepted" in response.text
    assert "REJECTED=true" in response.text


def test_the_ui_opens_with_the_token(token_app: Application) -> None:
    with _client(token_app, _LAN) as client:
        query = client.get(f"/?token={_TOKEN}")
        header = client.get("/", headers={"Authorization": f"Bearer {_TOKEN}"})
    for response in (query, header):
        assert response.status_code == 200
        assert "Maverick" in response.text
    assert '<form class="tokenbox" id="token-box" hidden' in query.text, (
        "the field ships with the page, hidden until a fetch comes back 401"
    )


def test_the_preview_is_closed_to_the_lan(token_app: Application) -> None:
    with _client(token_app, _LAN) as client:
        response = client.get("/api/displays/kitchen/preview.png")
    assert response.status_code == 401


def test_the_preview_opens_with_the_token(token_app: Application) -> None:
    with _client(token_app, _LAN) as client:
        query = client.get(f"/api/displays/kitchen/preview.png?token={_TOKEN}")
        header = client.get(
            "/api/displays/kitchen/preview.png",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    for response in (query, header):
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"


# --------------------------------------------------------------------------- #
# Ingress
# --------------------------------------------------------------------------- #

@pytest.fixture
def under_supervisor(monkeypatch):
    """What the Supervisor gives every app container, and nothing else has."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-token")


@pytest.mark.usefixtures("under_supervisor")
def test_ingress_needs_no_token(token_app: Application) -> None:
    """Home Assistant has already authenticated whoever is on that connection."""
    with _client(token_app, _INGRESS) as client:
        page = client.get("/")
        preview = client.get("/api/displays/kitchen/preview.png")
        displays = client.get("/api/displays")
    assert page.status_code == 200
    assert '<form class="tokenbox" id="token-box" hidden' in page.text
    assert preview.status_code == 200
    # The page's own fetches come through ingress too; gating them would leave
    # the buttons dead on exactly the path this exemption exists to keep working.
    assert displays.status_code == 200


def test_the_ingress_address_is_not_trusted_outside_the_app(token_app: Application) -> None:
    """Without the Supervisor there is no ingress, so the address means nothing.

    A LAN host can hold 172.30.32.2 on its own network and reach the published
    port from it; what it cannot do is be on the Supervisor's Docker network.
    """
    with _client(token_app, _INGRESS) as client:
        page = client.get("/")
        preview = client.get("/api/displays/kitchen/preview.png")
    assert page.status_code == 401
    assert preview.status_code == 401


@pytest.mark.usefixtures("under_supervisor")
def test_the_app_still_gates_the_lan(token_app: Application) -> None:
    """Running as an app exempts ingress, not the published port beside it."""
    with _client(token_app, _LAN) as client:
        assert client.get("/").status_code == 401
        assert client.get("/api/displays/kitchen/preview.png").status_code == 401


# --------------------------------------------------------------------------- #
# No token configured
# --------------------------------------------------------------------------- #

def test_without_a_token_nothing_is_gated(open_app: Application) -> None:
    with _client(open_app, _LAN) as client:
        page = client.get("/")
        preview = client.get("/api/displays/kitchen/preview.png")
        displays = client.get("/api/displays")
    assert page.status_code == 200
    assert '<form class="tokenbox" id="token-box" hidden' in page.text, (
        "the field ships either way; only a 401 reveals it"
    )
    assert preview.status_code == 200
    assert displays.status_code == 200
