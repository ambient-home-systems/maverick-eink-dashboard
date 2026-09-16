"""Home Assistant REST and WebSocket client.

Only the parts Maverick needs: verify the token, call a service, read state,
and subscribe to state changes so a render can follow the data rather than a
clock.

The WebSocket subscription is worth the complexity. Polling for "has the
temperature changed" on a five-second timer wakes the whole render stack
constantly; subscribing means a panel re-renders within a second of the value
it displays actually changing, and stays idle otherwise.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from ..config import HomeAssistantConfig
from .auth import TokenSource, build_token_source

log = logging.getLogger(__name__)

StateCallback = Callable[[str, dict[str, Any] | None, dict[str, Any] | None], Awaitable[None]]


class HomeAssistantError(RuntimeError):
    """A Home Assistant call failed."""


class _BearerAuth(httpx.Auth):
    """Attach a bearer token resolved at request time.

    httpx calls the sync flow unless the async one is defined, and resolving a
    token may need a network round trip, so only the async flow is implemented.
    """

    def __init__(self, resolve: Callable[[], Awaitable[str]]) -> None:
        self._resolve = resolve

    async def async_auth_flow(self, request: httpx.Request) -> Any:
        request.headers["Authorization"] = f"Bearer {await self._resolve()}"
        yield request


class HomeAssistantClient:
    """Thin async client over the HA REST and WebSocket APIs."""

    def __init__(
        self, config: HomeAssistantConfig, tokens: TokenSource | None = None
    ) -> None:
        self._config = config
        self._tokens = tokens or build_token_source(config)
        self._client: httpx.AsyncClient | None = None

    @property
    def config(self) -> HomeAssistantConfig:
        return self._config

    @property
    def tokens(self) -> TokenSource | None:
        return self._tokens

    async def _access_token(self) -> str:
        if self._tokens is None:
            raise HomeAssistantError(
                "No Home Assistant credential configured. Open Maverick's setup "
                "UI and use 'Link with Home Assistant', or set "
                "home_assistant.token to a long-lived access token."
            )
        return await self._tokens.token()

    async def _http(self) -> httpx.AsyncClient:
        # The Authorization header cannot be baked into the client any more: a
        # linked account's access token expires every 30 minutes, so it is
        # attached per request from whatever the token source holds now.
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.url,
                headers={"Content-Type": "application/json"},
                auth=_BearerAuth(self._access_token),
                verify=self._config.verify_ssl,
                timeout=30.0,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---------------------------------------------------------------- REST --

    async def check(self) -> dict[str, Any]:
        """Verify the URL and credential. Raises with an actionable message."""
        if self._tokens is None:
            raise HomeAssistantError(
                "No Home Assistant credential configured. Open Maverick's setup "
                "UI and use 'Link with Home Assistant', or create a long-lived "
                "access token under your profile -> Security and set "
                "home_assistant.token."
            )
        client = await self._http()
        try:
            response = await client.get("/api/")
        except httpx.RequestError as exc:
            raise HomeAssistantError(
                f"Cannot reach Home Assistant at {self._config.url}: {exc}"
            ) from exc
        if response.status_code == 401:
            raise HomeAssistantError(
                "Home Assistant rejected the token (401). Long-lived access "
                "tokens are bound to the instance that issued them."
                if self._tokens.kind == "long-lived token"
                else "Home Assistant rejected the token (401). The linked "
                "account may have been revoked under profile -> Security; "
                "link it again from Maverick's setup UI."
            )
        response.raise_for_status()
        return response.json()

    async def call_service(
        self,
        domain: str,
        service: str,
        data: dict[str, Any] | None = None,
        *,
        return_response: bool = False,
    ) -> Any:
        client = await self._http()
        params = {"return_response": "true"} if return_response else None
        response = await client.post(
            f"/api/services/{domain}/{service}", json=data or {}, params=params
        )
        if response.status_code >= 400:
            raise HomeAssistantError(
                f"{domain}.{service} failed ({response.status_code}): {response.text[:400]}"
            )
        return response.json() if response.content else None

    async def get_state(self, entity_id: str) -> dict[str, Any] | None:
        client = await self._http()
        response = await client.get(f"/api/states/{entity_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def list_states(self) -> list[dict[str, Any]]:
        """Every entity and its current state, from `GET /api/states`.

        The REST endpoint rather than the WebSocket `get_states`, because this
        is a one-shot read and `_http` already carries the credential and the
        base URL. Used to pick the entities a starter dashboard is built from
        (`src/maverick/lovelace/generator.py`) — a picker that offered
        `sensor.REPLACE_ME` would be a worse answer than no picker.
        """
        client = await self._http()
        response = await client.get("/api/states")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    async def set_state(
        self, entity_id: str, state: str, attributes: dict[str, Any] | None = None
    ) -> None:
        """Push a Maverick-owned entity into HA without MQTT.

        States created this way do not survive a Home Assistant restart, which
        is why MQTT discovery is the preferred path — but this needs no broker.
        """
        client = await self._http()
        response = await client.post(
            f"/api/states/{entity_id}",
            json={"state": state, "attributes": attributes or {}},
        )
        response.raise_for_status()

    async def fire_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        client = await self._http()
        response = await client.post(f"/api/events/{event_type}", json=data or {})
        response.raise_for_status()

    # ----------------------------------------------------------- WebSocket --

    async def watch_states(
        self,
        entity_ids: set[str],
        callback: StateCallback,
        stop: asyncio.Event | None = None,
    ) -> None:
        """Subscribe to ``state_changed`` and invoke ``callback`` for matches.

        Reconnects with backoff. Home Assistant restarts routinely (every config
        reload), so treating a dropped socket as fatal would mean a panel
        silently stops following its triggers until the add-on is restarted.
        """
        import websockets

        url = self._config.url.replace("http://", "ws://").replace("https://", "wss://")
        url = f"{url}/api/websocket"
        backoff = 1.0

        while stop is None or not stop.is_set():
            try:
                async with websockets.connect(url, max_size=8 * 1024 * 1024) as socket:
                    await self._ws_authenticate(socket)
                    await socket.send(
                        json.dumps({"id": 1, "type": "subscribe_events",
                                    "event_type": "state_changed"})
                    )
                    log.info("watching %d entities for changes", len(entity_ids))
                    backoff = 1.0

                    async for raw in socket:
                        message = json.loads(raw)
                        if message.get("type") != "event":
                            continue
                        data = message.get("event", {}).get("data", {})
                        entity_id = data.get("entity_id")
                        if entity_id in entity_ids:
                            await callback(entity_id, data.get("old_state"), data.get("new_state"))
                        if stop is not None and stop.is_set():
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure should retry
                log.warning("state watch disconnected (%s); retrying in %.0fs", exc, backoff)
                try:
                    await asyncio.wait_for(
                        stop.wait() if stop else asyncio.sleep(backoff), timeout=backoff
                    )
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 60.0)

    async def _ws_authenticate(self, socket: Any) -> None:
        greeting = json.loads(await socket.recv())
        if greeting.get("type") != "auth_required":
            raise HomeAssistantError(f"unexpected WebSocket greeting: {greeting}")
        await socket.send(
            json.dumps({"type": "auth", "access_token": await self._access_token()})
        )
        result = json.loads(await socket.recv())
        if result.get("type") != "auth_ok":
            raise HomeAssistantError(
                f"WebSocket auth failed: {result.get('message', result)}"
            )

    async def _ws_call(self, message_type: str, **payload: Any) -> Any:
        """Connect, authenticate and run one WebSocket command.

        For a caller that needs a single request/response, not a subscription
        that stays open for events — `watch_states` above opens its own
        connection instead, since staying open is the whole point of a
        subscription; the two share only the connect-and-authenticate steps,
        via `_ws_authenticate`. Uses the same token source and TLS handling as
        `watch_states`: neither passes an explicit `ssl` argument to
        `websockets.connect`, relying on the `wss://` scheme it is given.
        """
        import websockets

        url = self._config.url.replace("http://", "ws://").replace("https://", "wss://")
        url = f"{url}/api/websocket"
        async with websockets.connect(url, max_size=8 * 1024 * 1024) as socket:
            await self._ws_authenticate(socket)
            await socket.send(json.dumps({"id": 1, "type": message_type, **payload}))
            result = json.loads(await socket.recv())
            if not result.get("success"):
                error = result.get("error") or {}
                raise HomeAssistantError(
                    f"Home Assistant WebSocket command {message_type!r} failed: "
                    f"{error.get('message', result)}"
                )
            return result.get("result")

    async def list_dashboards(self) -> list[dict[str, Any]]:
        """Every Lovelace dashboard and its views.

        Two WebSocket commands, both found in Home Assistant's own source
        rather than recalled: `lovelace/dashboards/list`, registered in
        `homeassistant/components/lovelace/__init__.py` where
        `DashboardsCollectionWebSocket(dashboards_collection,
        "lovelace/dashboards", "dashboard", ...)` sets up the standard
        collection CRUD commands for the *extra* dashboards (created in the
        UI or added in YAML) — the built-in default dashboard is not one of
        them, since it lives at `url_path=None` internally and is never added
        to that collection. And `lovelace/config`, handled by
        `websocket_lovelace_config` in
        `homeassistant/components/lovelace/websocket.py`, which takes an
        optional `url_path` (omitted or `None` for the default dashboard) and
        returns that dashboard's configuration, including its `views`.

        A dashboard whose configuration cannot be fetched — a YAML-mode
        dashboard, one with no saved configuration yet, or any other failure
        — still appears, with an empty `views` list, rather than losing the
        whole picker over one broken dashboard. `lovelace/dashboards/list`
        itself failing is different: it means the picker has nothing to show
        at all, so that failure is not caught here and reaches the caller as
        a `HomeAssistantError` (`GET /api/ha/dashboards` turns it into a 503).

        Home Assistant's storage-mode migration can add an explicit entry for
        the default dashboard, with `url_path: "lovelace"`, to the same
        collection `lovelace/dashboards/list` reads
        (`homeassistant/components/lovelace/__init__.py`), so the default is
        tracked by `url_path` in a dict rather than always prepended, to avoid
        listing it twice with two different titles.
        """
        dashboards: dict[str, str] = {"lovelace": "Overview"}
        extra = await self._ws_call("lovelace/dashboards/list")
        for entry in extra:
            url_path = entry.get("url_path")
            if not url_path:
                continue
            dashboards[url_path] = entry.get("title") or url_path

        result: list[dict[str, Any]] = []
        for url_path, title in dashboards.items():
            views: list[dict[str, Any]] = []
            try:
                payload = {} if url_path == "lovelace" else {"url_path": url_path}
                config = await self._ws_call("lovelace/config", **payload)
            except HomeAssistantError:
                config = None
            if isinstance(config, dict):
                if url_path == "lovelace" and config.get("title"):
                    title = config["title"]
                for index, view in enumerate(config.get("views") or []):
                    view_path = view.get("path") or str(index)
                    views.append(
                        {
                            "path": f"/{url_path}/{view_path}",
                            "title": view.get("title") or view_path,
                        }
                    )
            result.append({"url_path": url_path, "title": title, "views": views})
        return result


__all__ = ["HomeAssistantClient", "HomeAssistantError"]
