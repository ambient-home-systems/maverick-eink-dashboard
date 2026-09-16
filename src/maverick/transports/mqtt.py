"""MQTT transport — push frames to always-on clients.

Suits anything mains-powered that can hold a subscription: a Raspberry Pi
driving an Inky, an ESP32 running ESPHome with an MQTT subscription, a custom
client. The frame is published to a per-display topic, with metadata alongside
it on a sibling topic so a client can decide whether it needs to redraw.

Frames are published retained. A client that reboots gets the current frame
immediately instead of a blank panel until the next scheduled render.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, ClassVar

from ..eink import Frame
from .base import DeliveryContext, DeliveryResult, OptionField, Transport, register

log = logging.getLogger(__name__)


def availability_topic(base_topic: str) -> str:
    """The retained online/offline topic for the service as a whole.

    Home Assistant's discovery payloads point every entity's availability at
    this topic, and it is what the broker publishes ``offline`` to on our
    behalf if we die. Defined here rather than on :class:`MqttDiscovery` so the
    engine can register the last will without importing the HA layer.
    """
    return f"{base_topic}/status"


class MqttPublisher:
    """A small shared wrapper over paho-mqtt's threaded client.

    paho is callback- and thread-based; everything else here is asyncio. Rather
    than bolt an async MQTT library on, publishes are bounced through the event
    loop's executor and the ``wait_for_publish`` handshake gives real delivery
    confirmation at QoS 1.
    """

    def __init__(self, config: Any, will: tuple[str, str] | None = None) -> None:
        self._config = config
        self._client: Any = None
        self._connected = asyncio.Event()
        #: (topic, payload) published by the broker if we disconnect uncleanly,
        #: so Home Assistant marks every display unavailable rather than showing
        #: a stale "last render" forever. paho registers a will as part of the
        #: CONNECT packet, so this must be supplied here: setting it after
        #: :meth:`start` has connected is silently ignored.
        self._will = will

    async def start(self) -> None:
        if self._client is not None:
            return
        import paho.mqtt.client as mqtt

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=self._config.client_id
        )
        if self._config.username:
            client.username_pw_set(self._config.username, self._config.password)
        if self._config.tls:
            client.tls_set()
        if self._will is not None:
            client.will_set(self._will[0], self._will[1], qos=1, retain=True)

        loop = asyncio.get_running_loop()

        def on_connect(_c: Any, _u: Any, _f: Any, reason: Any, _p: Any = None) -> None:
            if getattr(reason, "is_failure", False):
                log.error("MQTT connection refused: %s", reason)
            else:
                log.info("MQTT connected to %s:%s", self._config.host, self._config.port)
                loop.call_soon_threadsafe(self._connected.set)

        def on_disconnect(*_args: Any) -> None:
            loop.call_soon_threadsafe(self._connected.clear)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        # Reconnects are automatic; loop_start runs the network thread.
        await loop.run_in_executor(
            None, lambda: client.connect(self._config.host, self._config.port, 60)
        )
        client.loop_start()
        self._client = client
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=15)
        except TimeoutError as exc:
            raise RuntimeError(
                f"Timed out connecting to the MQTT broker at "
                f"{self._config.host}:{self._config.port}. Check mqtt.host, "
                "credentials, and that the broker is running."
            ) from exc

    async def stop(self) -> None:
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

    async def publish(
        self, topic: str, payload: bytes | str, retain: bool = True, qos: int = 1
    ) -> None:
        if self._client is None:
            await self.start()
        assert self._client is not None
        info = self._client.publish(topic, payload, qos=qos, retain=retain)
        if qos:
            await asyncio.get_running_loop().run_in_executor(None, info.wait_for_publish, 15)

    def subscribe(self, topic: str, handler: Any, qos: int = 0) -> None:
        if self._client is None:
            raise RuntimeError("MqttPublisher.start() was not awaited")
        self._client.message_callback_add(topic, handler)
        self._client.subscribe(topic, qos=qos)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()


@register
class MqttTransport(Transport):
    """Publish frames to an MQTT topic."""

    name = "mqtt"
    pushes = True
    description = "Publish frames to MQTT for always-on clients (Inky, Pi, custom)"
    options_doc: ClassVar[dict[str, str]] = {
        "topic": (
            "Base topic for this display. Unset uses `<mqtt.base_topic>/display/<display "
            "id>`. The frame is published retained to `<topic>/frame` and its metadata to "
            "`<topic>/meta`."
        ),
    }
    option_fields: ClassVar[dict[str, OptionField]] = {
        "topic": OptionField(advanced=True, label="Base topic"),
    }

    async def deliver(self, frame: Frame, context: DeliveryContext) -> DeliveryResult:
        publisher: MqttPublisher | None = context.services.get("mqtt")
        if publisher is None:
            return DeliveryResult.failure(
                "MQTT is not enabled. Set mqtt.enabled: true and configure the broker."
            )
        base = self.option("topic") or (
            f"{context.config.mqtt.base_topic}/display/{context.display.id}"
        )
        started = time.perf_counter()
        meta = {
            "id": context.display.id,
            "name": context.display.name,
            "width": frame.width,
            "height": frame.height,
            "format": frame.frame_format.value,
            "color_scheme": frame.palette.scheme.value,
            "checksum": frame.checksum,
            "bytes": len(frame.payload),
            "full_refresh": context.full_refresh,
            "sequence": context.sequence,
            "trigger": context.trigger,
        }
        try:
            await publisher.publish(f"{base}/meta", json.dumps(meta))
            await publisher.publish(f"{base}/frame", frame.payload)
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult.failure(f"MQTT publish to {base} failed: {exc}")
        return DeliveryResult.success(
            f"published to {base}/frame",
            bytes_sent=len(frame.payload),
            duration_s=time.perf_counter() - started,
        )

    async def probe(self, context: DeliveryContext) -> DeliveryResult:
        publisher: MqttPublisher | None = context.services.get("mqtt")
        if publisher is None:
            return DeliveryResult.failure("MQTT is not enabled")
        return (
            DeliveryResult.success("broker connected")
            if publisher.connected
            else DeliveryResult.failure("broker not connected")
        )


__all__ = ["MqttTransport", "MqttPublisher", "availability_topic"]
