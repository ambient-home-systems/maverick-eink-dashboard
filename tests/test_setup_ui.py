"""The setup UI behind Home Assistant's ingress, and when the credential is bad.

Two things this page has to get right, both of which it got wrong once and
neither of which any other test covers.

*It has to survive a path prefix.* Ingress serves the page at
``/api/hassio_ingress/<token>/`` and proxies to the app with that prefix
stripped, telling the app nothing about it: the Supervisor sends no
``X-Ingress-Path`` (``supervisor/api/ingress.py``, ``_init_header``) and builds
its upstream URL as ``http://<ip>:<port>/<path>`` (``_create_url``). So a
root-relative ``/api/...`` in the page resolves against Home Assistant's own
origin and never reaches the app; a relative ``api/...`` resolves against the
page's base, which is the prefix under ingress and ``/`` on the published port.
The route is ``/ingress/{token}/{path:.*}``, so that base always ends in a
slash and the relative form is safe.

*It has to offer the link when the credential is broken.* A client is built
whenever a credential is configured, working or not, so asking whether one
exists would claim "connected" for a credential Home Assistant rejects — and
hide the link card exactly when it is the one thing on the page that helps.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from maverick.app import Application
from maverick.config import Config
from maverick.server.api import create_app

#: src/href/fetch targets the rendered page carries.
_EMITTED = re.compile(
    r"""(?:src|href)=['"]([^'"]+)['"]|fetch\(['"`]([^'"`]+)['"`]"""
)


async def _noop(*args, **kwargs) -> None:
    return None


@pytest.fixture
def ui_config(tmp_path) -> Config:
    return Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "home_assistant": {"url": "http://homeassistant.local:8123"},
            "server": {"base_url": "http://192.168.1.10:5000"},
            "displays": [
                {"id": "kitchen", "panel": "trmnl-7in5", "transport": {"type": "file"}}
            ],
        }
    )


@pytest.fixture
def app(ui_config: Config, monkeypatch) -> Application:
    # The engine would otherwise start a browser pool and a scheduler.
    monkeypatch.setattr(Application, "start", _noop)
    monkeypatch.setattr(Application, "stop", _noop)
    return Application(ui_config)


def _page(app: Application) -> str:
    with TestClient(create_app(app)) as client:
        return client.get("/").text


def _targets(page: str) -> list[str]:
    return [a or b for a, b in _EMITTED.findall(page)]


# --------------------------------------------------------------------------- #
# Surviving the ingress prefix
# --------------------------------------------------------------------------- #

def test_the_page_emits_no_root_relative_app_paths(app: Application) -> None:
    """``/api/...`` in this page is a link that dies under ingress.

    It resolves against Home Assistant's origin, where nothing serves it, so
    the preview images break and *Link with Home Assistant* fails on a 404 it
    reports as a JSON parse error.
    """
    offenders = [t for t in _targets(_page(app)) if t.startswith("/api")]
    assert not offenders, (
        f"{offenders} are root-relative and will miss the ingress prefix. "
        "Emit them relative to the document instead (api/..., not /api/...)."
    )


def test_the_page_still_reaches_its_own_endpoints(app: Application) -> None:
    """Relative is only correct if the targets are real app routes.

    Guards against 'fixing' the paths by deleting them: each one is resolved
    the way a browser would against the page's base and must be served.
    """
    with TestClient(create_app(app)) as client:
        targets = [t for t in _targets(client.get("/").text) if t.startswith("api/")]
        assert targets, "the page emits no app endpoints at all"
        for target in targets:
            # A browser resolves `api/x` against the base `/`, giving `/api/x`.
            response = client.get(f"/{target}")
            assert response.status_code < 400, f"{target} -> {response.status_code}"


# --------------------------------------------------------------------------- #
# Offering the link when the credential does not work
# --------------------------------------------------------------------------- #

def test_a_broken_credential_still_offers_the_link(app: Application) -> None:
    """The state a user is stuck in: configured, rejected, and no way forward.

    `Engine.start` keeps the client after a failed check so the rest of the
    service can keep serving, which is why "is a client present" is the wrong
    question for this page to ask.
    """
    app.engine._ha = object()  # a client exists...
    app.engine._ha_ok = False  # ...but Home Assistant rejected it

    page = _page(app)
    assert "startLink(this)" in page, "the link card is missing for a broken credential"
    assert "Home Assistant not connected" in page


def test_a_working_credential_hides_the_link(app: Application) -> None:
    app.engine._ha = object()
    app.engine._ha_ok = True

    page = _page(app)
    assert "startLink(this)" not in page
    assert "Home Assistant connected" in page


def test_auth_status_reports_the_check_not_the_configuration(app: Application) -> None:
    """`/api/auth/status` answers the same question, so it must not differ."""
    app.engine._ha = object()
    app.engine._ha_ok = False
    with TestClient(create_app(app)) as client:
        assert client.get("/api/auth/status").json()["connected"] is False

    app.engine._ha_ok = True
    with TestClient(create_app(app)) as client:
        assert client.get("/api/auth/status").json()["connected"] is True


def test_the_base_url_blocker_says_where_to_set_it(app: Application) -> None:
    """A user reading this is in the app, not in a YAML file.

    The card names `home_assistant_token`'s option for the manual route, so the
    one blocking the button it is replacing has to do the same.
    """
    app.config.server.base_url = ""
    page = _page(app)
    assert "startLink(this)" not in page, "no base_url means nowhere to redirect back to"
    assert "Configuration tab" in page, "the blocker does not say where to set base_url"
