"""The MQTT last will has to reach the broker, or availability is a lie.

``ha/discovery.py`` points every entity's availability at ``<base>/status`` and
tells users a last will marks them unavailable if Maverick dies. paho carries
the will in the CONNECT packet, so a will registered after the client has
connected never reaches the broker at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from maverick.config import Config
from maverick.engine import Engine
from maverick.transports.mqtt import MqttPublisher, availability_topic


class _PublishInfo:
    """What paho's publish() hands back: something with a publish to wait on."""

    def wait_for_publish(self, timeout: float | None = None) -> None:
        del timeout  # nothing is in flight; the fake published synchronously
        return None


class FakeClient:
    """Records the calls MqttPublisher.start() makes, in order."""

    def __init__(self, *_args: Any, **kwargs: Any) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        #: (topic, payload, retain) per publish, in order, so a test can read
        #: back what reached the broker — including the empty retained payloads
        #: that retract a removed display's discovery config.
        self.published: list[tuple[str, Any, bool]] = []
        self.client_id = kwargs.get("client_id")
        self.on_connect: Any = None
        self.on_disconnect: Any = None

    def _record(self, name: str):
        def call(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))
        return call

    def __getattr__(self, name: str) -> Any:
        # Everything MqttPublisher touches is fire-and-forget except connect()
        # and publish(), which are defined below.
        return self._record(name)

    def connect(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("connect", args, kwargs))
        # paho invokes on_connect from its network thread once the broker
        # acknowledges; MqttPublisher waits on that before returning.
        self.on_connect(self, None, None, _Reason(), None)

    def publish(
        self, topic: str, payload: Any = None, qos: int = 0, retain: bool = False
    ) -> _PublishInfo:
        self.calls.append(("publish", (topic, payload), {"qos": qos, "retain": retain}))
        self.published.append((topic, payload, retain))
        # MqttPublisher awaits `info.wait_for_publish` at qos 1, so `None` —
        # what __getattr__'s recorder would return — is not enough.
        return _PublishInfo()

    @property
    def call_names(self) -> list[str]:
        return [name for name, _args, _kwargs in self.calls]

    def payload_on(self, topic: str) -> Any:
        """The payload of the last publish to ``topic``, or ``None``."""
        matches = [payload for t, payload, _retain in self.published if t == topic]
        return matches[-1] if matches else None

    def topics(self) -> list[str]:
        return [topic for topic, _payload, _retain in self.published]


class _Reason:
    is_failure = False


@pytest.fixture
def fake_clients(monkeypatch) -> list[FakeClient]:
    """Replace paho's Client so nothing opens a socket."""
    import paho.mqtt.client as paho

    created: list[FakeClient] = []

    def factory(*args: Any, **kwargs: Any) -> FakeClient:
        client = FakeClient(*args, **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(paho, "Client", factory)
    return created


async def test_will_is_set_before_connect(fake_clients) -> None:
    config = Config.model_validate({"mqtt": {"enabled": True, "base_topic": "maverick"}})
    publisher = MqttPublisher(config.mqtt, will=(availability_topic("maverick"), "offline"))

    await publisher.start()

    client = fake_clients[0]
    wills = [c for c in client.calls if c[0] == "will_set"]
    assert len(wills) == 1, "will_set must be called exactly once"
    _name, args, kwargs = wills[0]
    assert args == ("maverick/status", "offline")
    assert kwargs == {"qos": 1, "retain": True}

    names = client.call_names
    assert names.index("will_set") < names.index("connect"), (
        "paho sends the will in the CONNECT packet, so it must be registered first"
    )


async def test_will_survives_a_custom_base_topic(fake_clients) -> None:
    config = Config.model_validate({"mqtt": {"enabled": True, "base_topic": "panels/ink"}})
    publisher = MqttPublisher(config.mqtt, will=(availability_topic("panels/ink"), "offline"))

    await publisher.start()

    assert ("will_set", ("panels/ink/status", "offline"), {"qos": 1, "retain": True}) in (
        fake_clients[0].calls
    )


async def test_engine_start_registers_the_will(fake_clients, tmp_path, monkeypatch) -> None:
    """The engine, not Application, is what builds the publisher — and the will.

    This is the regression: the will used to be assigned to the publisher after
    Engine.start() had already connected it, so will_set was never called.
    """
    config = Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "mqtt": {"enabled": True, "base_topic": "maverick"},
            "displays": [],
        }
    )
    engine = Engine(config)
    # Keep the browser pool and Home Assistant out of it.
    monkeypatch.setattr("maverick.engine.DashboardRenderer", lambda *a, **k: object())

    await engine.start()

    assert engine.mqtt is not None
    assert ("will_set", ("maverick/status", "offline"), {"qos": 1, "retain": True}) in (
        fake_clients[0].calls
    )


async def test_will_matches_the_topic_discovery_advertises(fake_clients) -> None:
    """The will topic and the availability topic must not drift apart."""
    from maverick.ha import MqttDiscovery

    config = Config.model_validate({"mqtt": {"enabled": True, "base_topic": "maverick"}})
    publisher = MqttPublisher(config.mqtt, will=(availability_topic("maverick"), "offline"))
    discovery = MqttDiscovery(publisher, config)

    await publisher.start()

    _name, args, _kwargs = next(c for c in fake_clients[0].calls if c[0] == "will_set")
    assert args[0] == discovery.availability_topic
