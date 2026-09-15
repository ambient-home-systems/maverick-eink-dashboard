"""MQTT discovery: make every panel a real Home Assistant device.

This is the difference between "a tool that talks to Home Assistant" and "a
tool that belongs in Home Assistant". Without it, triggering a render from an
automation means defining a ``rest_command``, and knowing whether a panel is
healthy means reading the add-on log.

With it, each display arrives as a device carrying:

* ``button.<name>_refresh`` — press it from an automation, a script, a
  dashboard, or a voice assistant. This is the answer to "support for
  automations to trigger it".
* ``switch.<name>_scheduled_renders`` — pause the timeline without editing YAML.
  Useful for "stop refreshing the bedroom panel while we're asleep".
* ``image.<name>`` — what the panel is currently showing, visible in the HA UI.
  Invaluable when the panel is in another room.
* ``sensor.<name>_last_render`` / ``_status`` / ``_render_duration`` — enough to
  alert on a panel that has silently stopped updating.

Entities are published with ``retain`` so they survive a Home Assistant restart,
and a last-will marks them unavailable if Maverick dies.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..config import Config, DisplayConfig
from ..transports.mqtt import MqttPublisher, availability_topic

log = logging.getLogger(__name__)

MANUFACTURER = "Maverick"


class MqttDiscovery:
    """Publishes discovery config and state for every display."""

    def __init__(self, publisher: MqttPublisher, config: Config, version: str = "0.1.0") -> None:
        self._mqtt = publisher
        self._config = config
        self._version = version
        self._prefix = config.mqtt.discovery_prefix
        self._base = config.mqtt.base_topic

    # ------------------------------------------------------------- topics --

    @property
    def availability_topic(self) -> str:
        return availability_topic(self._base)

    def display_base(self, display_id: str) -> str:
        return f"{self._base}/display/{display_id}"

    def command_topic(self, display_id: str) -> str:
        return f"{self.display_base(display_id)}/command"

    def state_topic(self, display_id: str) -> str:
        return f"{self.display_base(display_id)}/state"

    # ---------------------------------------------------------- publishing --

    async def announce(self) -> None:
        await self._mqtt.publish(self.availability_topic, "online", retain=True)
        for display in self._config.enabled_displays:
            await self.announce_display(display)
        log.info("published MQTT discovery for %d display(s)", len(self._config.enabled_displays))

    async def announce_display(self, display: DisplayConfig) -> None:
        resolved = display.resolved()
        device = {
            "identifiers": [f"maverick_{display.id}"],
            "name": display.name,
            "manufacturer": MANUFACTURER,
            "model": resolved.profile.name,
            "sw_version": self._version,
            "configuration_url": self._config.server.base_url or None,
        }
        availability = [{"topic": self.availability_topic}]
        base = self.display_base(display.id)

        def config_topic(component: str, suffix: str) -> str:
            return f"{self._prefix}/{component}/maverick_{display.id}/{suffix}/config"

        common: dict[str, Any] = {
            "device": device,
            "availability": availability,
            "qos": 1,
        }

        await self._publish_config(
            config_topic("button", "refresh"),
            {
                **common,
                "name": "Refresh",
                "unique_id": f"maverick_{display.id}_refresh",
                "command_topic": self.command_topic(display.id),
                "payload_press": "refresh",
                "icon": "mdi:refresh",
                "entity_category": "config",
            },
        )
        await self._publish_config(
            config_topic("button", "full_refresh"),
            {
                **common,
                "name": "Full refresh",
                "unique_id": f"maverick_{display.id}_full_refresh",
                "command_topic": self.command_topic(display.id),
                "payload_press": "full_refresh",
                "icon": "mdi:television-clean",
                "entity_category": "config",
            },
        )
        await self._publish_config(
            config_topic("switch", "scheduled"),
            {
                **common,
                "name": "Scheduled renders",
                "unique_id": f"maverick_{display.id}_scheduled",
                "command_topic": self.command_topic(display.id),
                "state_topic": self.state_topic(display.id),
                "value_template": "{{ value_json.schedule_enabled }}",
                "payload_on": "schedule_on",
                "payload_off": "schedule_off",
                "state_on": True,
                "state_off": False,
                "icon": "mdi:timer-outline",
                "entity_category": "config",
            },
        )

        # An image entity lets you see what a panel in another room is showing.
        # url_topic is preferred: pushing a full frame through the broker on
        # every render is wasteful when an HTTP URL will do.
        if self._publishes_a_url():
            image_config = {
                **common,
                "name": "Screen",
                "unique_id": f"maverick_{display.id}_image",
                "url_topic": f"{base}/image_url",
                "icon": "mdi:image",
            }
        else:
            image_config = {
                **common,
                "name": "Screen",
                "unique_id": f"maverick_{display.id}_image",
                "image_topic": f"{base}/preview",
                "content_type": "image/png",
                "icon": "mdi:image",
            }
        await self._publish_config(config_topic("image", "screen"), image_config)

        sensors = [
            # key, name, device class, value template, icon, unit
            (
                "last_render", "Last render", "timestamp",
                "value_json.last_render_at", "mdi:clock-outline", None,
            ),
            (
                "status", "Status", None,
                "value_json.status", "mdi:information-outline", None,
            ),
            (
                "render_duration", "Render duration", "duration",
                "value_json.render_duration", "mdi:timer-sand", "s",
            ),
            (
                "ink_coverage", "Ink coverage", None,
                "value_json.ink_coverage", "mdi:water-percent", "%",
            ),
            (
                "frames", "Frames delivered", None,
                "value_json.sequence", "mdi:counter", None,
            ),
        ]
        for key, name, device_class, template, icon, unit in sensors:
            payload = {
                **common,
                "name": name,
                "unique_id": f"maverick_{display.id}_{key}",
                "state_topic": self.state_topic(display.id),
                "value_template": "{{ " + template + " }}",
                "icon": icon,
                "entity_category": "diagnostic",
            }
            if device_class:
                payload["device_class"] = device_class
            if unit:
                payload["unit_of_measurement"] = unit
                payload["state_class"] = "measurement"
            await self._publish_config(config_topic("sensor", key), payload)

        await self._publish_config(
            config_topic("binary_sensor", "problem"),
            {
                **common,
                "name": "Problem",
                "unique_id": f"maverick_{display.id}_problem",
                "state_topic": self.state_topic(display.id),
                "value_template": "{{ value_json.problem }}",
                "payload_on": True,
                "payload_off": False,
                "device_class": "problem",
                "entity_category": "diagnostic",
            },
        )

    def _publishes_a_url(self) -> bool:
        """Whether the image entity gets a URL, or the PNG bytes themselves.

        A URL only works if Home Assistant can fetch it, and it fetches with no
        credentials at all — an image entity has nowhere to put a token. So
        ``server.api_token``, which puts ``preview.png`` behind that token
        (`src/maverick/server/api.py`), sends the frame over the broker instead
        for the same reason an empty ``server.base_url`` does: there is no URL
        Home Assistant could usefully be given. ``announce_display`` and
        ``publish_image`` both ask, because a disagreement between them points
        the entity at a topic nothing is ever published to.
        """
        return bool(self._config.server.base_url) and not self._config.server.api_token

    async def _publish_config(self, topic: str, payload: dict[str, Any]) -> None:
        clean = {k: v for k, v in payload.items() if v is not None}
        await self._mqtt.publish(topic, json.dumps(clean), retain=True)

    # --------------------------------------------------------------- state --

    async def publish_state(self, display_id: str, state: dict[str, Any]) -> None:
        await self._mqtt.publish(self.state_topic(display_id), json.dumps(state), retain=True)

    async def publish_image(self, display_id: str, png: bytes) -> None:
        base = self.display_base(display_id)
        if self._publishes_a_url():
            url = (
                f"{self._config.server.base_url.rstrip('/')}"
                f"/api/displays/{display_id}/preview.png"
            )
            await self._mqtt.publish(f"{base}/image_url", url, retain=True)
        else:
            await self._mqtt.publish(f"{base}/preview", png, retain=True)

    async def offline(self) -> None:
        await self._mqtt.publish(self.availability_topic, "offline", retain=True)

    async def remove_display(self, display_id: str) -> None:
        """Retract discovery for a display removed from the config."""
        for component, suffixes in (
            ("button", ("refresh", "full_refresh")),
            ("switch", ("scheduled",)),
            ("image", ("screen",)),
            ("sensor", ("last_render", "status", "render_duration", "ink_coverage", "frames")),
            ("binary_sensor", ("problem",)),
        ):
            for suffix in suffixes:
                topic = f"{self._prefix}/{component}/maverick_{display_id}/{suffix}/config"
                await self._mqtt.publish(topic, "", retain=True)


__all__ = ["MqttDiscovery"]
