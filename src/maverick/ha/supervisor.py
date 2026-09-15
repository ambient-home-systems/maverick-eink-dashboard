"""What Maverick needs from the Supervisor: its options, and its ingress proxy.

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

The other thing an app has to know about the Supervisor is **how to recognise
a request that came through its ingress proxy**, because Home Assistant's own
login sits in front of that path and no token of ours is involved. Home
Assistant documents the answer as an address and nothing else: "Only
connections from ``172.30.32.2`` must be allowed. You should deny access to
all other IP addresses within your app server", alongside "Users are
previously authenticated via Home Assistant. Authentication is not required"
(*Presenting your app*, Ingress). The Supervisor's own source agrees on where
that address comes from: ``DOCKER_IPV4_NETWORK_MASK = IPv4Network(
"172.30.32.0/23")`` (``supervisor/const.py``) with the Supervisor itself as
host 2 of that network (``supervisor/docker/network.py``, the ``supervisor``
property), and the proxy opens a plain connection to the app container —
``http://{app.ip_address}:{app.ingress_port}/{path}``
(``supervisor/api/ingress.py``, ``_create_url``) — so the peer address the app
sees is the Supervisor's.

It is the *peer address* and not a header on purpose. ``_init_header`` in the
same file adds only ``X-Remote-User-Id``, ``X-Remote-User-Name`` and
``X-Remote-User-Display-Name``, and appends the connecting address to
``X-Forwarded-For``; it strips inbound copies of those three before proxying,
which protects the Supervisor's own trust in them and does nothing for ours.
Maverick publishes port 5000 as well (``app/config.yaml``), so anything on the
LAN can send those same headers straight to it. The peer address cannot be set
that way: it is the source of a completed TCP connection, so reaching us as
``172.30.32.2`` means being on the Supervisor's Docker network — the same
boundary Home Assistant tells apps to trust. The check is still made only
while `running_under_supervisor` is true, so that on any other host the
address is just an address somebody could hold.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"

#: The address the Supervisor's ingress proxy connects from; see the module
#: docstring for where this comes from and why it is trusted as a peer address
#: rather than as a header.
INGRESS_PEER = "172.30.32.2"


class SupervisorError(RuntimeError):
    """A Supervisor API call failed."""


def supervisor_token() -> str | None:
    """The token the Supervisor injects into every app container."""
    return os.environ.get("SUPERVISOR_TOKEN") or None


def running_under_supervisor() -> bool:
    return supervisor_token() is not None


def request_is_from_ingress(peer: str | None) -> bool:
    """Whether a request from ``peer`` arrived through the ingress proxy.

    ``peer`` is the address the connection came from, which for an ASGI
    application is ``request.client.host``. False whenever Maverick is not
    running as an app: outside the Supervisor's network nothing stops a
    machine from holding this address.
    """
    return running_under_supervisor() and peer == INGRESS_PEER


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
    "INGRESS_PEER",
    "SupervisorError",
    "current_options",
    "request_is_from_ingress",
    "running_under_supervisor",
    "save_options",
    "supervisor_token",
]
