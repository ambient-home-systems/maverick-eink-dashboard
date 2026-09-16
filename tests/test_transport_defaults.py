"""A display that names no transport gets the one its panel ships with.

`PanelProfile.default_transport` (`src/maverick/devices/panels.yaml`) has
always described how each panel is actually reached — `opendisplay` for a BLE
shelf label, `mqtt` for a Pi holding a subscription, `http_pull` for a
sleeping ESP32 — and the panel reference has always documented it as "the
transport a display uses if it sets none of its own". Nothing read it:
`TransportConfig.type` defaulted to the literal `"http_pull"`, so a display
was never *not* naming a transport, and a BLE tag published frames to an HTTP
endpoint nothing would ever fetch.

These tests pin the resolution both ways round: the panel supplies it, an
explicit `type` still wins, and what is looked up in the registry is the
resolved name rather than the raw field — which is `None` now, and not a
registry key.
"""

from __future__ import annotations

import pytest

from maverick.config import Config, DisplayConfig
from maverick.devices import get_panel


def display(**overrides) -> DisplayConfig:
    return DisplayConfig.model_validate({"id": "panel-under-test", **overrides})


def test_unset_transport_takes_the_panels_own():
    """A BLE shelf label delivers over BLE without being told to."""
    tag = display(panel="opendisplay-solum-4in2-bwr")
    assert tag.transport.type is None
    assert tag.transport_type == "opendisplay"
    assert get_panel("opendisplay-solum-4in2-bwr").default_transport == "opendisplay"


def test_an_explicit_transport_still_wins():
    """Naming one is how you override the catalogue, so it outranks it."""
    tag = display(
        panel="opendisplay-solum-4in2-bwr", transport={"type": "file", "path": "/tmp/frames"}
    )
    assert tag.transport_type == "file"


def test_panels_with_no_opinion_fall_back_to_http_pull():
    """`PanelProfile.default_transport` is itself defaulted, so this is the
    catalogue's answer rather than a second fallback in the config layer."""
    assert display(panel="generic-mono").transport_type == "http_pull"


def test_resolved_carries_the_transport_it_resolved_to():
    """Everything downstream reads `ResolvedDisplay`, so the resolution has to
    survive the merge rather than being recomputed by each caller."""
    resolved = display(panel="inky-impression-5in7").resolved()
    assert resolved.transport_type == "mqtt"


def test_frame_format_follows_the_resolved_transport():
    """`_default_format_for` is keyed on the transport, and it used to be
    handed the raw field. For a panel with no `default_format` that now means
    the *panel's* transport decides the wire format."""
    resolved = display(panel="opendisplay-solum-7in5-bwr").resolved()
    assert resolved.transport_type == "opendisplay"
    # `opendisplay` takes a PIL image, so PNG rather than a packed buffer.
    assert resolved.frame_format.value == "png"


def test_transport_options_survive_an_unset_type():
    """`extra="allow"` is what carries a transport's own options
    (`src/maverick/config.py`), and leaving `type` out must not disturb it."""
    tag = display(panel="opendisplay-solum-2in9-bwr", transport={"device_id": "abc123"})
    assert tag.transport_type == "opendisplay"
    assert tag.transport.model_dump()["device_id"] == "abc123"


def test_an_unknown_transport_is_still_rejected_at_use():
    """Optional does not mean unchecked: a misspelt name is not silently
    replaced by the panel's, because `or` only fires on an empty value."""
    tag = display(panel="generic-mono", transport={"type": "htp_pull"})
    assert tag.transport_type == "htp_pull"


def test_the_example_config_still_resolves_what_it_documents():
    """`config.example.yaml` names both transports explicitly; this is the
    guard that making the field optional did not change what it means."""
    config = Config.model_validate(
        {
            "displays": [
                {"id": "kitchen", "panel": "waveshare-7in5-mono"},
                {"id": "hallway-tag", "panel": "opendisplay-solum-2in9-bwr"},
            ]
        }
    )
    assert config.display("kitchen").transport_type == "http_pull"
    assert config.display("hallway-tag").transport_type == "opendisplay"


def test_a_bad_panel_is_still_what_fails_first():
    """`transport_type` reads the profile, so a display naming a panel that
    does not exist must fail at load rather than at first delivery.

    `DisplayConfig._defaults` calls `get_panel` for exactly this, and its
    `KeyError` carries the near-match suggestion — pydantic only converts
    `ValueError` into a validation error, so this one arrives as it was
    raised."""
    with pytest.raises(KeyError, match="Unknown panel"):
        DisplayConfig.model_validate({"id": "x", "panel": "no-such-panel"})
