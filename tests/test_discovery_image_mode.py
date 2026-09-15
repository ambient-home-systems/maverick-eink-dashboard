"""The image entity has to be told the mode Maverick actually publishes in.

``MqttDiscovery`` announces ``image.<name>`` one of two ways — a ``url_topic``
carrying a link to ``preview.png``, or an ``image_topic`` carrying the PNG
itself — and ``publish_image`` then fills one topic or the other. Disagree, and
the entity subscribes to a topic nothing is ever published to: an image that is
permanently unavailable, with nothing in any log to say why.

``server.api_token`` is the case that reaches this. Home Assistant fetches an
image URL with no credentials — an image entity has nowhere to put a token — so
a token makes the URL useless to it, exactly as an empty ``server.base_url``
does (`src/maverick/ha/discovery.py`).

The double here stands in for ``MqttPublisher`` rather than for paho's client,
the way ``tests/test_mqtt_will.py`` does: discovery touches nothing of the
publisher but ``publish``, and recording at that seam keeps the assertions
about discovery payloads instead of about CONNECT packets.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from maverick.config import Config
from maverick.ha import MqttDiscovery

_BASE_URL = "http://192.168.1.10:5000"
_IMAGE_CONFIG_TOPIC = "homeassistant/image/maverick_kitchen/screen/config"


class RecordingPublisher:
    """Records publishes instead of reaching a broker."""

    def __init__(self) -> None:
        self.published: list[tuple[str, Any]] = []

    async def publish(
        self, topic: str, payload: bytes | str, retain: bool = True, qos: int = 1
    ) -> None:
        del retain, qos  # discovery leaves both at the publisher's defaults
        self.published.append((topic, payload))

    def payload_for(self, topic: str) -> Any:
        return next((p for t, p in self.published if t == topic), None)

    def topics(self) -> list[str]:
        return [t for t, _p in self.published]


def _config(*, base_url: str, api_token: str) -> Config:
    return Config.model_validate(
        {
            "mqtt": {"enabled": True, "base_topic": "maverick"},
            "server": {"base_url": base_url, "api_token": api_token},
            "displays": [
                {"id": "kitchen", "panel": "trmnl-7in5", "transport": {"type": "file"}}
            ],
        }
    )


async def _announce(config: Config) -> RecordingPublisher:
    publisher = RecordingPublisher()
    discovery = MqttDiscovery(publisher, config)
    await discovery.announce_display(config.display("kitchen"))
    return publisher


async def _publish_image(config: Config) -> RecordingPublisher:
    publisher = RecordingPublisher()
    discovery = MqttDiscovery(publisher, config)
    await discovery.publish_image("kitchen", b"\x89PNG-not-really")
    return publisher


async def test_a_token_sends_the_bytes_over_the_broker() -> None:
    config = _config(base_url=_BASE_URL, api_token="s3cret-token")

    image = json.loads((await _announce(config)).payload_for(_IMAGE_CONFIG_TOPIC))

    assert image["image_topic"] == "maverick/display/kitchen/preview"
    assert image["content_type"] == "image/png"
    assert "url_topic" not in image, (
        "the entity would fetch preview.png unauthenticated and get a 401"
    )


async def test_a_token_makes_publish_image_fill_that_topic() -> None:
    config = _config(base_url=_BASE_URL, api_token="s3cret-token")

    publisher = await _publish_image(config)

    assert publisher.payload_for("maverick/display/kitchen/preview") == b"\x89PNG-not-really"
    assert "maverick/display/kitchen/image_url" not in publisher.topics()


async def test_without_a_token_the_url_is_still_preferred() -> None:
    """Pushing a frame through the broker on every render is the costlier mode."""
    config = _config(base_url=_BASE_URL, api_token="")

    image = json.loads((await _announce(config)).payload_for(_IMAGE_CONFIG_TOPIC))
    publisher = await _publish_image(config)

    assert image["url_topic"] == "maverick/display/kitchen/image_url"
    assert "image_topic" not in image
    assert publisher.payload_for("maverick/display/kitchen/image_url") == (
        f"{_BASE_URL}/api/displays/kitchen/preview.png"
    )


async def test_no_base_url_still_sends_the_bytes() -> None:
    """The branch that existed before the token reached this decision."""
    config = _config(base_url="", api_token="")

    image = json.loads((await _announce(config)).payload_for(_IMAGE_CONFIG_TOPIC))

    assert image["image_topic"] == "maverick/display/kitchen/preview"
    assert "url_topic" not in image


@pytest.mark.parametrize(
    ("base_url", "api_token"),
    [(_BASE_URL, ""), (_BASE_URL, "s3cret-token"), ("", ""), ("", "s3cret-token")],
)
async def test_the_announced_topic_is_the_one_that_gets_published_to(
    base_url: str, api_token: str
) -> None:
    """The invariant behind all of the above, over every combination."""
    config = _config(base_url=base_url, api_token=api_token)

    image = json.loads((await _announce(config)).payload_for(_IMAGE_CONFIG_TOPIC))
    publisher = await _publish_image(config)

    announced = image.get("url_topic") or image["image_topic"]
    assert publisher.topics() == [announced]
