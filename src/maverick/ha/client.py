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

log = logging.getLogger(__name__)

StateCallback = Callable[[str, dict[str, Any] | None, dict[str, Any] | None], Awaitable[None]]


class HomeAssistantError(RuntimeError):
    """A Home Assistant call failed."""


class HomeAssistantClient:
    """Thin async client over the HA REST and WebSocket APIs."""

    def __init__(self, config: HomeAssistantConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None

    @property
    def config(self) -> HomeAssistantConfig:
        return self._config

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._config.url,
                headers={
                    "Authorization": f"Bearer {self._config.token}",
                    "Content-Type": "application/json",
                },
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
        """Verify the URL and token. Raises with an actionable message."""
        if not self._config.token:
            raise HomeAssistantError(
                "No Home Assistant token configured. Create a long-lived access "
                "token under your profile -> Security, and set "
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
        await socket.send(json.dumps({"type": "auth", "access_token": self._config.token}))
        result = json.loads(await socket.recv())
        if result.get("type") != "auth_ok":
            raise HomeAssistantError(
                f"WebSocket auth failed: {result.get('message', result)}"
            )


__all__ = ["HomeAssistantClient", "HomeAssistantError"]
