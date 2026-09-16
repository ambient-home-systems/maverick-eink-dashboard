"""Onboarding a BLE tag without copying anything out of a URL.

Four pieces, each tested against the surface the setup UI uses:

* `Transport.option_fields` (`src/maverick/transports/base.py`), served by
  `GET /api/schema/display` under `fields`, which is what turns twelve text
  boxes into a mode picker, one required field and a fold;
* `HomeAssistantClient.list_devices` (`src/maverick/ha/client.py`) and
  `GET /api/ha/opendisplay/devices`, which read the device registry so the
  registry id is picked, not pasted — the payload shape is Home Assistant's
  `websocket_list_devices` (`homeassistant/helpers/device_registry.py`),
  modelled from source, since the suite has no Home Assistant to capture from;
* `guess_panel` (`src/maverick/devices/guess.py`), the catalogue id a model
  string points at;
* the probes: `POST /api/displays/probe` on a candidate, the OpenDisplay
  transport's registry check in `ha` mode, and its refusal of `ble` inside
  the Home Assistant app (`_resolve_mode`, `src/maverick/transports/opendisplay.py`).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from conftest import DISPLAY_ID, FakeRenderer
from fastapi.testclient import TestClient

from maverick import engine as engine_module
from maverick.app import Application
from maverick.config import Config, DisplayConfig, HomeAssistantConfig
from maverick.devices.guess import guess_panel
from maverick.ha.client import HomeAssistantClient
from maverick.server.api import create_app
from maverick.transports import DeliveryContext
from maverick.transports.opendisplay import OpenDisplayTransport

#: `config/device_registry/list`, three entries: two OpenDisplay tags and a
#: device of another integration that must be filtered out.
DEVICE_REGISTRY: list[dict[str, Any]] = [
    {
        "id": "0a1b2c3d4e5f60718293a4b5c6d7e8f9",
        "name": "OpenDisplay AA:BB",
        "name_by_user": "Hallway tag",
        "model": 'ST-GR29000 2.9" BWR',
        "manufacturer": "Solum",
        "sw_version": "0.3",
        "hw_version": None,
        "identifiers": [["opendisplay", "AA:BB:CC:DD:EE:FF"]],
    },
    {
        "id": "ffeeddccbbaa99887766554433221100",
        "name": "Fridge label",
        "name_by_user": None,
        "model": "OpenDisplay Flex 2.9",
        "manufacturer": "OpenDisplay",
        "sw_version": None,
        "hw_version": None,
        "identifiers": [["opendisplay", "11:22:33:44:55:66"]],
    },
    {
        "id": "deadbeefdeadbeefdeadbeefdeadbeef",
        "name": "Living room proxy",
        "name_by_user": None,
        "model": "esp32",
        "manufacturer": "espressif",
        "identifiers": [["esphome", "proxy"]],
    },
]


async def _fake_ws_call(self: HomeAssistantClient, message_type: str, **payload: Any) -> Any:
    del self, payload
    assert message_type == "config/device_registry/list"
    return DEVICE_REGISTRY


# --------------------------------------------------------------------------- #
# option_fields
# --------------------------------------------------------------------------- #


def test_the_schema_serves_how_to_ask_for_each_option(tmp_path, monkeypatch) -> None:
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(Application, "start", _noop)
    monkeypatch.setattr(Application, "stop", _noop)
    config = Config.model_validate({"data_dir": str(tmp_path / "data")})
    with TestClient(create_app(Application(config))) as client:
        schema = client.get("/api/schema/display").json()
    opendisplay = schema["transports"]["opendisplay"]
    assert opendisplay["mode_option"] == "mode"
    fields = opendisplay["fields"]
    assert set(fields) == set(opendisplay["options"]), "every documented option is described"
    assert fields["mode"]["kind"] == "select"
    assert fields["mode"]["choices"] == ["auto", "ha", "ble"]
    assert fields["device_id"] == {
        "modes": ["auto", "ha"], "required": True, "advanced": False, "kind": "ha_device",
        "choices": [], "default": None, "label": "Tag in Home Assistant",
        "integration": "opendisplay",
    }
    assert fields["mac"]["modes"] == ["auto", "ble"] and fields["mac"]["required"] is True
    for key in ("media_dir", "media_root", "media_source_prefix", "rotation"):
        assert fields[key]["advanced"] is True and fields[key]["modes"] == ["auto", "ha"]
    for key in ("encryption_key", "timeout", "max_attempts", "scan_timeout", "device_name"):
        assert fields[key]["advanced"] is True and fields[key]["modes"] == ["auto", "ble"]
    # A transport that declares nothing still answers, with nothing.
    assert schema["transports"]["fake"]["fields"] == {}
    assert schema["transports"]["fake"]["mode_option"] == ""
    assert schema["transports"]["webhook"]["fields"]["url"]["required"] is True


def test_every_option_field_names_a_documented_option() -> None:
    from maverick.transports import available_transports

    for name, cls in available_transports().items():
        extra = set(cls.option_fields) - set(cls.options_doc)
        assert not extra, f"{name} describes {extra} in option_fields but not in options_doc"
        if cls.mode_option:
            assert cls.mode_option in cls.option_fields


# --------------------------------------------------------------------------- #
# guess_panel
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('ST-GR29000 2.9" BWR', "opendisplay-solum-2in9-bwr"),
        ("OpenDisplay Flex 2.9", "opendisplay-flex-2in9"),
        ('2.9" BWR Solum', "opendisplay-solum-2in9-bwr"),
        ("XIAO ePaper 7.5", "opendisplay-xiao-7in5"),
        ("4.26 mono", "opendisplay-mono-4in26"),
        ("7.3 Spectra E6", "opendisplay-spectra-7in3"),
        ("2.6 red label", "opendisplay-solum-2in6-bwr"),
        ("OpenDisplay_A1B2", None),
        ("", None),
    ],
)
def test_guess_panel_from_a_model_string(text: str, expected: str | None) -> None:
    assert guess_panel(text, prefer_transport="opendisplay") == expected


def test_guess_panel_without_a_transport_preference_reaches_the_whole_catalogue() -> None:
    assert guess_panel("Waveshare 7.5in mono") == "waveshare-7in5-mono"
    assert guess_panel("7.5in BWR waveshare") == "waveshare-7in5-bwr"


# --------------------------------------------------------------------------- #
# list_devices and the route
# --------------------------------------------------------------------------- #


async def test_list_devices_keeps_the_integrations_devices_only(monkeypatch) -> None:
    monkeypatch.setattr(HomeAssistantClient, "_ws_call", _fake_ws_call)
    client = HomeAssistantClient(HomeAssistantConfig(url="http://ha.local:8123", token="t"))
    devices = await client.list_devices("opendisplay")
    assert [d["id"] for d in devices] == [
        "ffeeddccbbaa99887766554433221100", "0a1b2c3d4e5f60718293a4b5c6d7e8f9",
    ], "sorted by name, the proxy left out"
    hallway = next(d for d in devices if d["name"] == "Hallway tag")
    assert hallway["model"] == 'ST-GR29000 2.9" BWR'
    assert "identifiers" not in hallway, "only what a picker needs"


@pytest.fixture
def served(minimal_config: Config, monkeypatch, legible_image):
    """A started `Application` over HTTP with a fake Home Assistant client."""
    minimal_config.display(DISPLAY_ID).schedule.render_on_start = False
    minimal_config.displays.append(
        DisplayConfig.model_validate(
            {
                "id": "fridge",
                "panel": "opendisplay-solum-2in9-bwr",
                "transport": {"type": "opendisplay", "mode": "ha",
                              "device_id": "ffeeddccbbaa99887766554433221100"},
                "schedule": {"render_on_start": False},
            }
        )
    )
    renderer = FakeRenderer(legible_image)
    monkeypatch.setattr(
        engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer
    )
    app = Application(minimal_config)
    with TestClient(create_app(app)) as client:
        fake = HomeAssistantClient(HomeAssistantConfig(url="http://ha.local:8123", token="t"))
        monkeypatch.setattr(HomeAssistantClient, "_ws_call", _fake_ws_call)
        app.engine._ha = fake
        app.engine._ha_ok = True
        yield SimpleNamespace(client=client, app=app)


def test_the_route_adds_a_panel_guess_and_the_display_already_using_the_tag(served) -> None:
    devices = served.client.get("/api/ha/opendisplay/devices").json()
    by_name = {d["name"]: d for d in devices}
    assert by_name["Hallway tag"]["panel_guess"] == "opendisplay-solum-2in9-bwr"
    assert by_name["Hallway tag"]["display_id"] is None
    assert by_name["Fridge label"]["display_id"] == "fridge"
    assert "Living room proxy" not in by_name


def test_the_route_answers_503_without_home_assistant(served) -> None:
    served.app.engine._ha_ok = False
    assert served.client.get("/api/ha/opendisplay/devices").status_code == 503


def test_the_environment_route_describes_this_host(served, monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    body = served.client.get("/api/environment").json()
    assert body["addon"] is False
    assert body["home_assistant"] is True
    assert body["esphome_dashboard"] is None
    assert body["bluetooth_scan"] is False, "the opendisplay extra is not installed here"


# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #


def test_probing_a_candidate_checks_the_id_against_the_registry(served) -> None:
    body = {
        "id": "new-tag",
        "panel": "opendisplay-solum-2in9-bwr",
        "transport": {"type": "opendisplay", "mode": "ha",
                      "device_id": "0a1b2c3d4e5f60718293a4b5c6d7e8f9"},
    }
    ok = served.client.post("/api/displays/probe", json=body).json()
    assert ok["ok"] is True
    assert "Hallway tag" in ok["detail"]
    assert not any(d.id == "new-tag" for d in served.app.config.displays), "nothing saved"

    body["transport"]["device_id"] = "sensor.hallway_tag_battery"
    wrong = served.client.post("/api/displays/probe", json=body).json()
    assert wrong["ok"] is False
    assert "not a device of the OpenDisplay integration" in wrong["detail"]

    del body["transport"]["device_id"]
    unset = served.client.post("/api/displays/probe", json=body).json()
    assert unset["ok"] is False
    assert unset["detail"] == "transport.device_id is not set"


def test_probing_a_configured_display_runs_its_transport(served) -> None:
    fridge = served.client.post("/api/displays/fridge/probe").json()
    assert fridge["ok"] is True
    assert "Fridge label" in fridge["detail"]
    assert served.client.post("/api/displays/nope/probe").status_code == 404
    # The fake transport's probe is the base class's: a success with a note.
    kitchen = served.client.post(f"/api/displays/{DISPLAY_ID}/probe").json()
    assert kitchen["ok"] is True


def test_a_candidate_naming_an_unknown_transport_is_a_422(served) -> None:
    body = {"id": "x", "panel": "generic-mono", "transport": {"type": "carrier-pigeon"}}
    assert served.client.post("/api/displays/probe", json=body).status_code == 422


def _context(config: Config, display_id: str, ha: object | None) -> DeliveryContext:
    return DeliveryContext(
        display=config.display(display_id).resolved(), config=config,
        services={"ha": ha} if ha is not None else {},
    )


async def test_ble_is_refused_inside_the_app(minimal_config: Config, monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    minimal_config.displays.append(
        DisplayConfig.model_validate(
            {"id": "tag", "panel": "opendisplay-solum-2in9-bwr",
             "transport": {"type": "opendisplay", "mode": "ble", "mac": "AA:BB:CC:DD:EE:FF"}}
        )
    )
    transport = OpenDisplayTransport({"mode": "ble", "mac": "AA:BB:CC:DD:EE:FF"})
    context = _context(minimal_config, "tag", None)
    result = await transport.probe(context)
    assert result.ok is False
    assert "not available in the Home Assistant app" in result.detail
    delivered = await transport.deliver(object(), context)  # type: ignore[arg-type]
    assert delivered.ok is False
    assert delivered.detail == result.detail


async def test_auto_resolves_to_ha_inside_the_app_even_without_a_connection(
    minimal_config: Config, monkeypatch
) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    minimal_config.displays.append(
        DisplayConfig.model_validate(
            {"id": "tag", "panel": "opendisplay-solum-2in9-bwr"}
        )
    )
    transport = OpenDisplayTransport({})
    result = await transport.probe(_context(minimal_config, "tag", None))
    assert result.ok is False
    assert result.detail.startswith("opendisplay mode 'ha' needs a Home Assistant connection")


async def test_a_ble_probe_without_a_target_says_so_before_scanning(
    minimal_config: Config, monkeypatch
) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    minimal_config.displays.append(
        DisplayConfig.model_validate(
            {"id": "tag", "panel": "opendisplay-solum-2in9-bwr",
             "transport": {"type": "opendisplay", "mode": "ble"}}
        )
    )
    import sys
    import types

    # A stand-in `opendisplay` module, so the probe gets past the import and
    # the message under test is the one about the missing target.
    monkeypatch.setitem(sys.modules, "opendisplay", types.SimpleNamespace())
    transport = OpenDisplayTransport({"mode": "ble"})
    result = await transport.probe(_context(minimal_config, "tag", None))
    assert result.ok is False
    assert result.detail.startswith("neither transport.mac nor transport.device_name is set")
