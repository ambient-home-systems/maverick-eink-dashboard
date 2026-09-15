"""Writing add-on options back through the Supervisor.

When Maverick runs as a Home Assistant app, its connection settings live in
the app's Configuration tab, not in ``maverick.yaml``: ``run.sh`` turns them
into environment variables that the config file reads through ``${VAR}``
substitution. That is deliberately one source of truth, so a credential the
setup UI obtains has to go back to the same place rather than being written
into the YAML alongside it.

The Supervisor allows this without any extra permission. Its security
middleware checks an ``api_bypass`` list *before* it checks whether an app
was granted ``hassio_api``, and ``/addons/self/options`` is on that list
(`supervisor/api/middleware/security.py`), so the ``SUPERVISOR_TOKEN`` every
app already gets is enough to change its own options — and only its own.

Outside the Supervisor (a bare ``maverick serve``) none of this applies:
`running_under_supervisor` is false and the caller persists the credential
some other way, or asks the user to.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"


class SupervisorError(RuntimeError):
    """A Supervisor API call failed."""


def supervisor_token() -> str | None:
    """The token the Supervisor injects into every app container."""
    return os.environ.get("SUPERVISOR_TOKEN") or None


def running_under_supervisor() -> bool:
    return supervisor_token() is not None


async def _request(method: str, path: str, json: dict[str, Any] | None = None) -> Any:
    token = supervisor_token()
    if token is None:
        raise SupervisorError("Not running as a Home Assistant app.")
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.request(
                method,
                f"{SUPERVISOR_URL}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=json,
            )
        except httpx.RequestError as exc:
            raise SupervisorError(f"Cannot reach the Supervisor: {exc}") from exc
    if response.status_code >= 400:
        raise SupervisorError(
            f"Supervisor {method} {path} failed ({response.status_code}): "
            f"{response.text[:300]}"
        )
    payload = response.json()
    if isinstance(payload, dict) and payload.get("result") == "error":
        raise SupervisorError(
            f"Supervisor {method} {path} failed: {payload.get('message', payload)}"
        )
    return payload.get("data") if isinstance(payload, dict) else payload


async def current_options() -> dict[str, Any]:
    """The app's options as the Supervisor currently holds them."""
    data = await _request("GET", "/addons/self/info")
    options = data.get("options") if isinstance(data, dict) else None
    return dict(options) if isinstance(options, dict) else {}


async def save_options(updates: dict[str, Any]) -> None:
    """Merge ``updates`` into the app's stored options.

    The Supervisor *replaces* the options dictionary rather than merging into
    it, so the current set is read first; posting only the changed keys would
    silently wipe every other setting the user has configured.
    """
    merged = await current_options()
    merged.update(updates)
    await _request("POST", "/addons/self/options", json={"options": merged})
    log.info("saved %s to the app options", ", ".join(sorted(updates)))


__all__ = [
    "SupervisorError",
    "current_options",
    "running_under_supervisor",
    "save_options",
    "supervisor_token",
]
