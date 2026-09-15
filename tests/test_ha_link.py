"""Linking a Home Assistant account from the setup UI.

The flow exists because the credential Maverick needs is not one an app can
mint for itself: rendering loads the real dashboard in a browser, so it needs a
*frontend* session, and the supervisor token every app is handed authenticates
the REST API only. The IndieAuth redirect flow is the way to get one without
the user copying a long-lived token by hand.

Nothing here talks to a real Home Assistant. The token endpoint is stubbed, so
what is under test is Maverick's half: how the client_id and redirect_uri are
derived, that the callback cannot be driven by anyone who did not start it, and
that a rotating access token does not leak browser contexts.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from maverick.app import Application
from maverick.config import Config
from maverick.ha import auth
from maverick.server.api import create_app


@pytest.fixture
def linkable_config(tmp_path) -> Config:
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
def client(linkable_config: Config, monkeypatch) -> TestClient:
    # The engine would otherwise start a browser pool and a scheduler.
    monkeypatch.setattr(Application, "start", _noop)
    monkeypatch.setattr(Application, "stop", _noop)
    return TestClient(create_app(Application(linkable_config)))


async def _noop(*args, **kwargs) -> None:
    return None


# --------------------------------------------------------------------------- #
# Deriving the client_id and redirect_uri
# --------------------------------------------------------------------------- #

def test_client_id_is_the_base_url_with_a_path() -> None:
    """Home Assistant requires a path component and rejects a bare host."""
    assert auth.client_id_for("http://192.168.1.10:5000") == "http://192.168.1.10:5000/"
    assert auth.client_id_for("http://192.168.1.10:5000/") == "http://192.168.1.10:5000/"


def test_redirect_uri_shares_the_client_id_origin() -> None:
    """Same scheme and netloc is what avoids Home Assistant fetching the page.

    `verify_redirect_uri` approves a redirect on the client_id's own origin
    outright; anything else means Home Assistant has to fetch the client_id URL
    and find a link tag, which Maverick does not serve.
    """
    base = "http://192.168.1.10:5000"
    client_id = auth.client_id_for(base)
    redirect = auth.redirect_uri_for(base)
    assert redirect == "http://192.168.1.10:5000/api/auth/callback"
    assert redirect.startswith(client_id)


@pytest.mark.parametrize("bad", ["", "192.168.1.10:5000", "ftp://host/", "not a url"])
def test_an_unusable_base_url_is_refused(bad: str) -> None:
    with pytest.raises(auth.AuthError):
        auth.client_id_for(bad)


def test_status_reports_what_is_missing(client: TestClient) -> None:
    body = client.get("/api/auth/status").json()
    assert body["linked"] is False
    assert body["kind"] == "none"
    assert body["can_link"] is True
    assert body["client_id"] == "http://192.168.1.10:5000/"


def test_status_explains_an_unset_base_url(linkable_config: Config, monkeypatch) -> None:
    linkable_config.server.base_url = ""
    monkeypatch.setattr(Application, "start", _noop)
    monkeypatch.setattr(Application, "stop", _noop)
    with TestClient(create_app(Application(linkable_config))) as client:
        body = client.get("/api/auth/status").json()
    assert body["can_link"] is False
    assert "base_url" in body["reason"]


def test_start_returns_an_authorize_url(client: TestClient) -> None:
    body = client.get("/api/auth/start").json()
    assert body["authorize_url"].startswith(
        "http://homeassistant.local:8123/auth/authorize?"
    )
    assert "client_id=http%3A%2F%2F192.168.1.10%3A5000%2F" in body["authorize_url"]
    assert "redirect_uri=http%3A%2F%2F192.168.1.10%3A5000%2Fapi%2Fauth%2Fcallback" in (
        body["authorize_url"]
    )


def test_start_refuses_without_a_base_url(linkable_config: Config, monkeypatch) -> None:
    linkable_config.server.base_url = ""
    monkeypatch.setattr(Application, "start", _noop)
    monkeypatch.setattr(Application, "stop", _noop)
    with TestClient(create_app(Application(linkable_config))) as client:
        assert client.get("/api/auth/start").status_code == 400


# --------------------------------------------------------------------------- #
# The callback
# --------------------------------------------------------------------------- #

def test_the_callback_rejects_a_state_it_did_not_mint(client: TestClient) -> None:
    """The nonce is what authenticates this route.

    Home Assistant knows nothing of `server.api_token`, so the callback cannot
    sit behind it; an unknown state must therefore be refused outright.
    """
    response = client.get("/api/auth/callback?code=abc&state=forged")
    assert response.status_code == 400
    assert "not one Maverick started" in response.text


def test_a_state_cannot_be_replayed(client: TestClient, monkeypatch) -> None:
    async def fake_exchange(url, client_id, code, *, verify_ssl=True):
        return auth.Grant(access_token="at", expires_in=1800, refresh_token="rt")

    async def fake_relink(self):
        return {"version": "2026.9.0"}

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    monkeypatch.setattr("maverick.engine.Engine.relink", fake_relink)

    state = _state_from(client)
    assert client.get(f"/api/auth/callback?code=abc&state={state}").status_code == 200
    # Second time with the same nonce: spent.
    assert client.get(f"/api/auth/callback?code=abc&state={state}").status_code == 400


def test_a_successful_callback_stores_the_grant(
    client: TestClient, linkable_config: Config, monkeypatch
) -> None:
    async def fake_exchange(url, client_id, code, *, verify_ssl=True):
        return auth.Grant(access_token="at", expires_in=1800, refresh_token="rt")

    async def fake_relink(self):
        return {"version": "2026.9.0"}

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    monkeypatch.setattr("maverick.engine.Engine.relink", fake_relink)

    state = _state_from(client)
    response = client.get(f"/api/auth/callback?code=abc&state={state}")
    assert response.status_code == 200
    assert linkable_config.home_assistant.refresh_token == "rt"
    assert linkable_config.home_assistant.client_id == "http://192.168.1.10:5000/"


def test_a_failed_exchange_leaves_the_config_alone(
    client: TestClient, linkable_config: Config, monkeypatch
) -> None:
    async def fake_exchange(url, client_id, code, *, verify_ssl=True):
        raise auth.AuthError("Home Assistant rejected the token request (400): nope")

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)

    state = _state_from(client)
    response = client.get(f"/api/auth/callback?code=abc&state={state}")
    assert response.status_code == 400
    assert linkable_config.home_assistant.refresh_token == ""


def _state_from(client: TestClient) -> str:
    """Start a link and pull the nonce back out of the authorize URL."""
    from urllib.parse import parse_qs, urlsplit

    url = client.get("/api/auth/start").json()["authorize_url"]
    return parse_qs(urlsplit(url).query)["state"][0]


# --------------------------------------------------------------------------- #
# Token sources
# --------------------------------------------------------------------------- #

def test_a_long_lived_token_is_used_as_is() -> None:
    from maverick.config import HomeAssistantConfig

    source = auth.build_token_source(HomeAssistantConfig(token="abc"))
    assert isinstance(source, auth.StaticToken)
    assert source.kind == "long-lived token"


def test_a_linked_account_wins_over_a_pasted_token() -> None:
    """Both present means the user linked more recently than they edited YAML."""
    from maverick.config import HomeAssistantConfig

    source = auth.build_token_source(
        HomeAssistantConfig(token="abc", refresh_token="rt", client_id="http://x:1/")
    )
    assert isinstance(source, auth.RefreshingToken)


def test_a_refresh_token_without_a_client_id_falls_back() -> None:
    """Home Assistant needs the client_id on every refresh, so half is unusable."""
    from maverick.config import HomeAssistantConfig

    source = auth.build_token_source(
        HomeAssistantConfig(token="abc", refresh_token="rt")
    )
    assert isinstance(source, auth.StaticToken)


def test_no_credential_at_all_is_not_an_error() -> None:
    """The service must start unlinked, or the setup UI cannot do the linking."""
    from maverick.config import HomeAssistantConfig

    assert auth.build_token_source(HomeAssistantConfig()) is None


@pytest.mark.asyncio
async def test_an_expiring_token_is_refreshed(monkeypatch) -> None:
    calls = []

    async def fake_refresh(url, client_id, refresh_token, *, verify_ssl=True):
        calls.append(refresh_token)
        return auth.Grant(access_token=f"at{len(calls)}", expires_in=1800)

    monkeypatch.setattr(auth, "refresh_grant", fake_refresh)
    source = auth.RefreshingToken("http://ha:8123", "http://x:1/", "rt")

    assert await source.token() == "at1"
    # Still inside the window: no second call.
    assert await source.token() == "at1"
    assert len(calls) == 1

    # Expired: refreshed, and the original refresh token is reused because a
    # refresh grant does not issue a new one.
    source._expires_at = time.time()
    assert await source.token() == "at2"
    assert calls == ["rt", "rt"]


@pytest.mark.asyncio
async def test_a_linked_bundle_lets_the_frontend_refresh_itself(monkeypatch) -> None:
    """clientId and refresh_token in the bundle are what make that possible.

    Without them a render that outlives the 30-minute access token bounces to
    the login screen, which screenshots as a blank frame.
    """
    async def fake_refresh(url, client_id, refresh_token, *, verify_ssl=True):
        return auth.Grant(access_token="at", expires_in=1800)

    monkeypatch.setattr(auth, "refresh_grant", fake_refresh)
    source = auth.RefreshingToken("http://ha:8123", "http://x:1/", "rt")

    fields = await source.bundle_fields()
    assert fields["clientId"] == "http://x:1/"
    assert fields["refresh_token"] == "rt"
    assert 0 < fields["expires_in"] <= 1800


@pytest.mark.asyncio
async def test_a_long_lived_bundle_carries_no_refresh_token() -> None:
    """There is nothing to refresh with, so the frontend must not try."""
    fields = await auth.StaticToken("abc").bundle_fields()
    assert fields["access_token"] == "abc"
    assert fields["refresh_token"] == ""
    assert fields["clientId"] is None


# --------------------------------------------------------------------------- #
# The renderer must not key browser contexts on a rotating token
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_a_refreshed_token_reuses_the_browser_context(monkeypatch, tmp_path) -> None:
    """A linked account's access token rotates every half hour.

    Browser contexts are cached forever by key and only dropped explicitly
    (`BrowserPool.drop_context`), so if the auth bundle were part of that key
    every refresh would strand a dead context in the pool — a Chromium context
    leak on a Raspberry Pi. The bundle goes on the page instead, which still
    runs before the frontend's first script.
    """
    from maverick.config import Config
    from maverick.render.dashboard import DashboardRenderer

    config = Config.model_validate(
        {
            "data_dir": str(tmp_path),
            "home_assistant": {
                "url": "http://ha:8123",
                "refresh_token": "rt",
                "client_id": "http://x:1/",
            },
            "displays": [
                {"id": "kitchen", "panel": "trmnl-7in5", "transport": {"type": "file"}}
            ],
        }
    )

    issued = []

    async def fake_refresh(url, client_id, refresh_token, *, verify_ssl=True):
        issued.append(f"at{len(issued) + 1}")
        return auth.Grant(access_token=issued[-1], expires_in=1800)

    monkeypatch.setattr(auth, "refresh_grant", fake_refresh)

    pool = _RecordingPool()
    renderer = DashboardRenderer(config.home_assistant, pool)
    display = config.display("kitchen").resolved()

    await renderer.render(display)
    source = renderer._tokens
    source._expires_at = time.time()  # force the next call to refresh
    await renderer.render(display)

    assert issued == ["at1", "at2"], "the token should have rotated"
    assert len(set(pool.keys)) == 1, f"context key changed with the token: {pool.keys}"
    # The rotated token still reached the page.
    assert "at1" in pool.page_scripts[0] and "at2" in pool.page_scripts[1]


class _RecordingPool:
    """A BrowserPool stand-in that records keys and page-level init scripts."""

    def __init__(self) -> None:
        self.keys: list[str] = []
        self.page_scripts: list[str] = []

    def page(self, key, viewport, scale=1.0, init_scripts=None, ignore_https_errors=False):
        self.keys.append(key)
        return _RecordingContext(self)

    async def drop_context(self, key: str) -> None:  # pragma: no cover
        pass


class _RecordingContext:
    def __init__(self, pool: _RecordingPool) -> None:
        self._pool = pool

    async def __aenter__(self):
        return _FakePage(self._pool)

    async def __aexit__(self, *exc_info) -> bool:
        return False


class _FakePage:
    """Just enough Playwright page surface for one default render."""

    def __init__(self, pool: _RecordingPool) -> None:
        self._pool = pool
        self.url = "http://ha:8123/lovelace/0"

    async def add_init_script(self, script: str) -> None:
        self._pool.page_scripts.append(script)

    async def goto(self, url, **kwargs) -> None:
        pass

    async def evaluate(self, script, *args):
        # _verify_authenticated asks whether a login form is on the page.
        return False

    async def wait_for_selector(self, selector, **kwargs) -> None:
        pass

    async def wait_for_timeout(self, ms: int) -> None:
        pass

    async def screenshot(self, **kwargs) -> bytes:
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (800, 480), "white").save(buffer, format="PNG")
        return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Applying a new credential without a restart
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_relink_adopts_the_new_credential(tmp_path, monkeypatch) -> None:
    """Linking has to take effect immediately.

    Telling the user to restart the app after pressing the button is exactly
    the friction the button exists to remove, so `Engine.relink` rebuilds the
    client and the renderer in place. This also exercises the browser-pool
    teardown, which is easy to get wrong because it is never hit by a test that
    only renders.
    """
    from maverick.config import Config
    from maverick.engine import Engine

    config = Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "home_assistant": {"url": "http://ha:8123"},
            "displays": [
                {"id": "kitchen", "panel": "trmnl-7in5", "transport": {"type": "file"}}
            ],
        }
    )
    engine = Engine(config)

    async def fake_check(self):
        return {"version": "2026.9.0"}

    monkeypatch.setattr("maverick.ha.client.HomeAssistantClient.check", fake_check)

    # Nothing configured yet: the engine starts anyway, unlinked.
    await engine.start()
    assert engine.ha is None
    assert engine.tokens is None

    config.home_assistant.refresh_token = "rt"
    config.home_assistant.client_id = "http://x:1/"
    info = await engine.relink()

    assert info["version"] == "2026.9.0"
    assert engine.tokens is not None
    assert engine.tokens.kind == "linked account"
    await engine.stop()


@pytest.mark.asyncio
async def test_relink_without_a_credential_is_refused(tmp_path) -> None:
    from maverick.config import Config
    from maverick.engine import Engine
    from maverick.ha import HomeAssistantError

    config = Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "displays": [
                {"id": "kitchen", "panel": "trmnl-7in5", "transport": {"type": "file"}}
            ],
        }
    )
    engine = Engine(config)
    await engine.start()
    with pytest.raises(HomeAssistantError):
        await engine.relink()
    await engine.stop()
