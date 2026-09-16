"""The Home Assistant app's own options, read by the service that they configure.

Maverick's connection settings live in the app's Configuration tab rather than
in ``maverick.yaml``. The Supervisor writes them into ``/data/options.json``
inside the container, and this module turns that file into the environment
variables the starter config substitutes — ``HA_URL``, ``MQTT_HOST`` and the
rest (``app/rootfs/usr/share/maverick/maverick.yaml``, and ``expand_env`` in
``src/maverick/config.py``). The names are exactly the ones ``app/run.sh``
exported before, so the file a user edits needs no migration and one source of
truth is kept: the options describe the connections, the YAML describes
everything else.

Doing the translation here removes bashio from the chain, and with it three of
the seven 0.2.x fixes (``CHANGELOG.md``), none of which were Maverick's own
semantics: ``bashio::config key ''`` hands back the *string* ``"null"`` for an
unset option, because bashio reads its own fallback as ``${2:-null}`` (bashio
``lib/config.sh``) and ``:-`` substitutes on an empty argument too; that string
then sails past every ``${VAR:-default}`` in the config and is stored as a
value. A missing key, a JSON ``null`` and an empty string all mean *unset*
here, and unset means the empty string, which is what the config file's
defaults are written to catch.

Two values are derived rather than read, both the way ``run.sh`` derived them:

* **base_url** — the address a panel that pulls frames is told to fetch from,
  which is never the container's own. Without the option, the host's first
  IPv4 address is read from ``GET /network/info`` and the prefix length
  stripped: the Supervisor reports each address as ``address.with_prefixlen``,
  so ``192.168.1.10/24`` (``supervisor/api/network.py``, ``ip4config_struct``).
* **MQTT** — the explicit ``mqtt_host`` and friends win; otherwise the
  Mosquitto broker app, when installed, hands its host and credentials over
  through ``GET /services/mqtt``; otherwise MQTT is off, which is allowed and
  only means displays do not appear as Home Assistant devices.

**What lets those two calls through.** They are not granted by the same thing,
and only one of them is what ``hassio_api: true`` in ``app/config.yaml`` buys:

* ``/network/info`` is reached by the role check. An app at the default role
  may call ``^/.+/info$`` and nothing else (``supervisor/api/middleware/
  security.py``, ``_V1_PATTERNS.role_access[ROLE_DEFAULT]``), and the role is
  only consulted at all because the app asked for ``hassio_api``.
* ``/services/mqtt`` never reaches the role check: ``/services.*`` is on the
  ``api_bypass`` list in the same file, which is tested before the role. What
  grants it is the ``services: mqtt:want`` declaration in ``app/config.yaml`` —
  ``_check_access`` in ``supervisor/api/services.py`` answers ``403 No access
  to mqtt service!`` unless the calling app declared the service. So removing
  ``hassio_api`` would cost the derived ``base_url`` but not the broker
  hand-off, and removing ``mqtt:want`` the other way round.

A Supervisor that will not answer is not exceptional: ``GET /services/mqtt``
is answered ``400 Service not enabled`` whenever no app provides the service
(``supervisor/api/services.py``, ``get_service``), which is the ordinary
"Mosquitto is not installed" case. Every call therefore degrades to the
fallback the option describes, with a line in the log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Final

from .supervisor import api_get

log = logging.getLogger(__name__)

#: Where the Supervisor writes the app's options inside its container.
DEFAULT_OPTIONS_PATH: Final = Path("/data/options.json")

#: Every key in ``app/config.yaml``'s ``schema``, and the environment variable
#: it becomes. The MQTT four are read only when ``mqtt_host`` is set; see
#: :func:`_mqtt` for what stands in when it is not.
OPTION_VARIABLES: Final[dict[str, str]] = {
    "home_assistant_url": "HA_URL",
    "home_assistant_token": "HA_TOKEN",
    "home_assistant_refresh_token": "HA_REFRESH_TOKEN",
    "home_assistant_client_id": "HA_CLIENT_ID",
    "log_level": "MAVERICK_LOG_LEVEL",
    "api_token": "MAVERICK_API_TOKEN",
    "base_url": "MAVERICK_BASE_URL",
    "mqtt_host": "MQTT_HOST",
    "mqtt_port": "MQTT_PORT",
    "mqtt_username": "MQTT_USERNAME",
    "mqtt_password": "MQTT_PASSWORD",
}

#: Set by :func:`load_app_options`, but no option of its own: whether MQTT is
#: configured at all is decided by the two sources above, not asked for.
DERIVED_VARIABLES: Final = ("MQTT_ENABLED",)

#: What ``app/config.yaml`` ships under ``options:`` for the two keys that are
#: never unset. ``${HA_URL}`` carries no ``:-`` default in the starter config,
#: because there is no sensible configuration without it.
DEFAULT_HA_URL: Final = "http://homeassistant:8123"
DEFAULT_LOG_LEVEL: Final = "info"

#: The broker the Mosquitto app runs, and the port both it and the option
#: default to; the same pair the starter config writes after ``:-``.
DEFAULT_MQTT_HOST: Final = "core-mosquitto"
DEFAULT_MQTT_PORT: Final = "1883"

#: The port the app publishes (``ports:`` in ``app/config.yaml``), which is
#: what a derived ``base_url`` has to name.
PUBLISHED_PORT: Final = 5000


def load_app_options(path: Path = DEFAULT_OPTIONS_PATH) -> dict[str, str]:
    """Read the app's options and return the variables the config file reads.

    Every variable in :data:`OPTION_VARIABLES` and :data:`DERIVED_VARIABLES` is
    present in the result, empty for an option that is not set, so a caller
    never has to distinguish "absent" from "empty" — and neither does the
    config file, whose ``${VAR:-default}`` fallbacks treat the two alike
    (``expand_env`` in ``src/maverick/config.py``).
    """
    options = _read(path)

    values = {
        "HA_URL": _option(options, "home_assistant_url") or DEFAULT_HA_URL,
        "HA_TOKEN": _option(options, "home_assistant_token"),
        "HA_REFRESH_TOKEN": _option(options, "home_assistant_refresh_token"),
        "HA_CLIENT_ID": _option(options, "home_assistant_client_id"),
        "MAVERICK_LOG_LEVEL": _option(options, "log_level") or DEFAULT_LOG_LEVEL,
        "MAVERICK_API_TOKEN": _option(options, "api_token"),
    }

    # Starting without a credential is allowed on purpose: the setup UI can
    # obtain one itself (Link with Home Assistant) and it can only do that
    # while the service is running, so this is a warning and not a refusal.
    if not values["HA_TOKEN"] and not values["HA_REFRESH_TOKEN"]:
        log.warning("No Home Assistant credential yet, so rendering will fail.")
        log.warning("Open this app's web UI and press 'Link with Home Assistant'.")
        log.warning(
            "You can instead paste a long-lived access token (your profile, "
            "Security tab) into the home_assistant_token option."
        )

    values["MAVERICK_BASE_URL"] = _base_url(_option(options, "base_url"))
    values.update(_mqtt(options))
    return values


def apply_app_options(path: Path = DEFAULT_OPTIONS_PATH) -> dict[str, str]:
    """Put :func:`load_app_options` into ``os.environ``, and return what it set.

    This has to run before the config is loaded, because the config file is
    what consumes the variables. The options win over anything already in the
    environment: under the Supervisor the Configuration tab is where a user
    changes these, and a stale variable inherited from somewhere else would
    quietly outrank the tab they just edited.
    """
    values = load_app_options(path)
    os.environ.update(values)
    return values


# ------------------------------------------------------------------ reading --

def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("the top level is not a JSON object")
    except (OSError, ValueError) as exc:
        log.warning(
            "Could not read the app's options from %s (%s); every option will "
            "be treated as unset.", path, exc,
        )
        return {}
    return payload


def _option(options: dict[str, Any], key: str) -> str:
    """One option as a string, with missing, ``null`` and ``""`` all unset.

    The three are the same question — "did the user fill this in?" — and the
    Supervisor produces all three: a key with no default is absent until it is
    set, the setup UI clears one by writing ``null`` through
    ``/addons/self/options``, and a text field cleared by hand is ``""``. The
    Supervisor's service payload is read the same way, an anonymous broker
    having no ``username`` to hand over.
    """
    value = options.get(key)
    if value is None:
        return ""
    return str(value)


# ---------------------------------------------------------------- base URL --

def _base_url(explicit: str) -> str:
    if explicit:
        return explicit
    address = _host_ipv4()
    if address:
        derived = f"http://{address}:{PUBLISHED_PORT}"
        log.info("base_url is not set; panels will be told to fetch from %s", derived)
        return derived
    log.warning("base_url is not set and the host address could not be read;")
    log.warning("panels that pull frames will not know where to fetch from.")
    return ""


def _host_ipv4() -> str:
    """The host's first IPv4 address, without its prefix length.

    Interfaces are taken in the order the Supervisor lists them, and only the
    host's own are considered: the ``docker`` block of the same payload holds
    the app network's ``172.30.32.0/23`` (``supervisor/api/network.py``), which
    no panel on the LAN can reach.
    """
    info = _supervisor_get("/network/info")
    if not isinstance(info, dict):
        return ""
    for interface in info.get("interfaces") or ():
        if not isinstance(interface, dict):
            continue
        ipv4 = interface.get("ipv4")
        addresses = ipv4.get("address") if isinstance(ipv4, dict) else None
        for address in addresses or ():
            bare = str(address).split("/")[0].strip()
            if bare:
                return bare
    return ""


# -------------------------------------------------------------------- MQTT --

def _mqtt(options: dict[str, Any]) -> dict[str, str]:
    host = _option(options, "mqtt_host")
    if host:
        port = _option(options, "mqtt_port") or DEFAULT_MQTT_PORT
        log.info("MQTT: using the broker from the app options (%s:%s)", host, port)
        return {
            "MQTT_ENABLED": "true",
            "MQTT_HOST": host,
            "MQTT_PORT": port,
            "MQTT_USERNAME": _option(options, "mqtt_username"),
            "MQTT_PASSWORD": _option(options, "mqtt_password"),
        }

    service = _supervisor_get("/services/mqtt")
    service = service if isinstance(service, dict) else {}
    host = _option(service, "host")
    if host:
        port = _option(service, "port") or DEFAULT_MQTT_PORT
        log.info(
            "MQTT: using the Mosquitto broker app (%s:%s); displays will appear "
            "as devices", host, port,
        )
        return {
            "MQTT_ENABLED": "true",
            "MQTT_HOST": host,
            "MQTT_PORT": port,
            "MQTT_USERNAME": _option(service, "username"),
            "MQTT_PASSWORD": _option(service, "password"),
        }

    log.info("MQTT: no broker configured and the Mosquitto broker app is not installed;")
    log.info("displays will not appear as Home Assistant devices. Set mqtt_host to change that.")
    return {
        "MQTT_ENABLED": "false",
        "MQTT_HOST": DEFAULT_MQTT_HOST,
        "MQTT_PORT": DEFAULT_MQTT_PORT,
        "MQTT_USERNAME": "",
        "MQTT_PASSWORD": "",
    }


# -------------------------------------------------------------- supervisor --

def _supervisor_get(path: str) -> Any | None:
    """``GET path`` from the Supervisor, or ``None`` when it will not answer.

    Synchronous on purpose: this runs before the CLI has started an event loop
    (``_load`` in ``src/maverick/cli.py``), and the caller is one blocking read
    of two at most.
    """
    try:
        return asyncio.run(api_get(path))
    except Exception as exc:  # noqa: BLE001
        # Broad on purpose, and not a warning. Every derived value has a
        # fallback the option's own description promises, so nothing here is
        # worth refusing to start over — and a service that will not start
        # cannot be fixed from its own web UI, which is where the fallbacks
        # are overridden. The commonest cause is the ordinary no-broker case
        # described in the module docstring; the caller says what the missing
        # answer costs.
        log.info("The Supervisor did not answer GET %s (%s).", path, exc)
        return None


__all__ = [
    "DEFAULT_OPTIONS_PATH",
    "DERIVED_VARIABLES",
    "OPTION_VARIABLES",
    "apply_app_options",
    "load_app_options",
]
