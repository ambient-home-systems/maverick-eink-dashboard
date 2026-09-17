"""The ESPHome hand-off: the described document, its secrets, and *Send to ESPHome*.

`describe_esphome` (`src/maverick/esphome/generator.py`) is what the setup UI's
*Install on device* step is drawn from, and `src/maverick/esphome/install.py`
is what its *Send to ESPHome* button does. Neither compiles or flashes
anything, and these tests hold the file-level promises: the token is a
`!secret` and not a literal, the download buffer never exceeds ESPHome's cap,
a file the user edited is not replaced without being asked, and
`secrets.yaml` is read and never written.

`esphome config` itself is not run here — `scripts/check_esphome.py` does
that, in CI's `esphome` job — but the generator's output shape is pinned so
that job cannot pass on a document this suite has not seen.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maverick.app import Application
from maverick.config import Config
from maverick.esphome import describe_esphome, esphome_applicable
from maverick.esphome import install as esphome_install
from maverick.esphome.generator import DOWNLOAD_BUFFER_MAX
from maverick.server.api import create_app

_TOKEN = "s3cret-token"


async def _noop(*args, **kwargs) -> None:
    return None


def _config(tmp_path, **server) -> Config:
    return Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "server": {"base_url": "http://192.168.1.10:5000", **server},
            "displays": [
                {"id": "kitchen", "panel": "waveshare-7in5-mono", "transport": {"type": "file"}},
                {"id": "colour", "panel": "waveshare-7in5-bwr", "transport": {"type": "file"}},
                {"id": "reader", "panel": "kindle-basic", "transport": {"type": "http_pull"}},
                {"id": "tag", "panel": "opendisplay-solum-2in9-bwr"},
                {"id": "big", "panel": "waveshare-10in3-gray16", "transport": {"type": "file"}},
            ],
        }
    )


@pytest.fixture
def app(tmp_path, monkeypatch) -> Application:
    monkeypatch.setattr(Application, "start", _noop)
    monkeypatch.setattr(Application, "stop", _noop)
    return Application(_config(tmp_path, api_token=_TOKEN, esphome_dir=str(tmp_path / "esphome")))


# --------------------------------------------------------------------------- #
# describe_esphome
# --------------------------------------------------------------------------- #


def test_the_token_is_a_secret_reference_and_its_value_travels_separately(tmp_path) -> None:
    config = _config(tmp_path, api_token=_TOKEN)
    described = describe_esphome(config.display("kitchen").resolved(), config)
    assert "request_headers:" in described["yaml"]
    assert "Authorization: !secret maverick_authorization" in described["yaml"]
    assert _TOKEN not in described["yaml"]
    by_name = {entry["name"]: entry for entry in described["secrets"]}
    assert by_name["maverick_authorization"]["value"] == f"Bearer {_TOKEN}"
    assert by_name["wifi_ssid"]["value"] is None
    assert list(by_name) == ["wifi_ssid", "wifi_password", "api_key", "maverick_authorization"]


def test_no_token_means_no_header_and_three_secrets(tmp_path) -> None:
    config = _config(tmp_path)
    described = describe_esphome(config.display("kitchen").resolved(), config)
    assert "request_headers" not in described["yaml"]
    assert [entry["name"] for entry in described["secrets"]] == [
        "wifi_ssid", "wifi_password", "api_key",
    ]


def test_the_download_buffer_never_exceeds_esphomes_cap(tmp_path) -> None:
    """`online_image.buffer_size` is `cv.int_range(256, 65536)` in ESPHome."""
    config = _config(tmp_path)
    colour = describe_esphome(config.display("colour").resolved(), config)
    assert colour["decoded_bytes"] > DOWNLOAD_BUFFER_MAX
    assert colour["needs_psram"] is True
    assert f"buffer_size: {DOWNLOAD_BUFFER_MAX}" in colour["yaml"]
    assert "\npsram:\n" in colour["yaml"]
    # A classic ESP32 board has quad PSRAM; octal is an S3 feature.
    assert "mode: octal" not in colour["yaml"]
    mono = describe_esphome(config.display("kitchen").resolved(), config)
    assert mono["needs_psram"] is False
    assert f"buffer_size: {mono['decoded_bytes']}" in mono["yaml"]
    assert "psram:" not in mono["yaml"]


def test_an_s3_board_gets_octal_psram(tmp_path) -> None:
    config = _config(tmp_path)
    config.display("colour").esphome.board = "esp32-s3-devkitc-1"
    described = describe_esphome(config.display("colour").resolved(), config)
    assert "mode: octal" in described["yaml"]


def test_the_online_image_keys_are_esphomes(tmp_path) -> None:
    """The two keys `esphome config` rejected before CI ran it."""
    config = _config(tmp_path, api_token=_TOKEN)
    yaml = describe_esphome(config.display("kitchen").resolved(), config)["yaml"]
    assert "\n    headers:" not in yaml
    assert "buffer_size_rx: 8192" in yaml


def test_a_panel_without_a_driver_is_said_so(tmp_path) -> None:
    config = _config(tmp_path)
    described = describe_esphome(config.display("big").resolved(), config)
    assert described["model_known"] is False
    assert described["model"] is None
    assert "placeholder" in described["yaml"]


def test_the_spectra_panel_uses_the_epaper_spi_platform(tmp_path) -> None:
    config = Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "displays": [
                {"id": "s", "panel": "waveshare-7in3-spectra", "transport": {"type": "file"}}
            ],
        }
    )
    described = describe_esphome(config.display("s").resolved(), config)
    assert described["platform"] == "epaper_spi"
    assert "platform: epaper_spi" in described["yaml"]
    assert "model: 7.3in-Spectra-E6" in described["yaml"]


def test_applicable_means_a_pull_display_on_an_esp32_panel(tmp_path) -> None:
    config = _config(tmp_path)
    assert esphome_applicable(config.display("kitchen").resolved()) is True
    assert esphome_applicable(config.display("reader").resolved()) is False, "a Kindle"
    assert esphome_applicable(config.display("tag").resolved()) is False, "a BLE tag"


# --------------------------------------------------------------------------- #
# install.py
# --------------------------------------------------------------------------- #


def test_destinations_standalone_are_the_configured_directory_only(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert esphome_install.destinations("") == []
    found = esphome_install.destinations(str(tmp_path / "esphome"))
    assert [d.id for d in found] == ["configured"]
    assert found[0].writable is True, "the parent exists, so the directory can be made"


def test_destinations_in_the_app_are_esphome_in_ha_config(tmp_path, monkeypatch) -> None:
    """The Device Builder reads `esphome/` in Home Assistant's config folder.

    Its manifest maps `config:rw` and its start script runs
    `esphome-device-builder /config/esphome` (see the module docstring of
    `src/maverick/esphome/install.py`); the app sees that folder at
    `/homeassistant`. Offered before the add-on has made the directory, since
    its start script does, and never the add-on's own `/addon_configs` folder,
    which it does not read.
    """
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    root = tmp_path / "homeassistant"
    root.mkdir()
    monkeypatch.setattr(esphome_install, "HOMEASSISTANT_CONFIG", root)
    monkeypatch.setattr(esphome_install, "SHARE_DIR", tmp_path / "share" / "esphome")
    found = esphome_install.destinations("")
    assert [d.id for d in found] == ["esphome", "share"]
    assert found[0].kind == "addon"
    assert found[0].path == root / "esphome"
    assert found[0].writable is True, "the parent exists, so the directory can be made"
    assert found[0].to_json()["exists"] is False


def test_without_the_mapping_only_the_share_folder_is_left(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    monkeypatch.setattr(esphome_install, "HOMEASSISTANT_CONFIG", tmp_path / "homeassistant")
    monkeypatch.setattr(esphome_install, "SHARE_DIR", tmp_path / "share" / "esphome")
    assert [d.id for d in esphome_install.destinations("")] == ["share"]


def test_the_api_key_is_made_up_fresh_and_is_a_valid_esphome_key(tmp_path) -> None:
    """`api.encryption.key` is 32 bytes in base64; the user should not need openssl."""
    import base64

    config = _config(tmp_path)
    first = describe_esphome(config.display("kitchen").resolved(), config)
    second = describe_esphome(config.display("kitchen").resolved(), config)
    keys = [
        {e["name"]: e["value"] for e in d["secrets"]}["api_key"] for d in (first, second)
    ]
    for key in keys:
        assert len(base64.b64decode(key, validate=True)) == 32
    assert keys[0] != keys[1]
    assert keys[0] not in first["yaml"], "the value goes in secrets.yaml, never the file"


def test_install_writes_and_refuses_to_replace_edits(tmp_path) -> None:
    target = esphome_install.Destination("t", tmp_path / "esphome", "configured", "the directory")
    path = esphome_install.install(target, "kitchen-panel.yaml", "a: 1\n", overwrite=False)
    assert path.read_text() == "a: 1\n"
    # Same content again: nothing to ask about.
    esphome_install.install(target, "kitchen-panel.yaml", "a: 1\n", overwrite=False)
    with pytest.raises(esphome_install.ExistsError):
        esphome_install.install(target, "kitchen-panel.yaml", "a: 2\n", overwrite=False)
    assert path.read_text() == "a: 1\n", "the user's file survived the refusal"
    esphome_install.install(target, "kitchen-panel.yaml", "a: 2\n", overwrite=True)
    assert path.read_text() == "a: 2\n"


def test_missing_secrets_reads_and_never_writes(tmp_path) -> None:
    names = ["wifi_ssid", "wifi_password", "api_key", "maverick_authorization"]
    assert esphome_install.missing_secrets(tmp_path, names) is None, "no file yet"
    secrets = tmp_path / "secrets.yaml"
    secrets.write_text('wifi_ssid: "home"\nwifi_password: "pw"\n')
    before = secrets.read_text()
    assert esphome_install.missing_secrets(tmp_path, names) == ["api_key", "maverick_authorization"]
    assert secrets.read_text() == before
    secrets.write_text("not: [valid")
    assert esphome_install.missing_secrets(tmp_path, names) is None, "unreadable reads as absent"


# --------------------------------------------------------------------------- #
# the routes
# --------------------------------------------------------------------------- #


def test_the_describe_route_lists_destinations_with_their_missing_secrets(
    app: Application, tmp_path
) -> None:
    (tmp_path / "esphome").mkdir()
    (tmp_path / "esphome" / "secrets.yaml").write_text('wifi_ssid: "x"\n')
    with TestClient(create_app(app)) as client:
        body = client.get(
            "/api/displays/kitchen/esphome", headers={"Authorization": f"Bearer {_TOKEN}"}
        ).json()
    assert body["applicable"] is True
    assert body["dashboard"] is None, "not under the Supervisor"
    assert body["addon"] is False
    (destination,) = body["destinations"]
    assert destination["id"] == "configured"
    assert destination["installed"] is False
    assert destination["missing_secrets"] == ["wifi_password", "api_key", "maverick_authorization"]


def test_the_install_route_writes_then_answers_409_for_an_edited_file(
    app: Application, tmp_path
) -> None:
    headers = {"Authorization": f"Bearer {_TOKEN}"}
    with TestClient(create_app(app)) as client:
        first = client.post(
            "/api/displays/kitchen/esphome/install",
            json={"destination": "configured"}, headers=headers,
        )
        assert first.status_code == 200, first.text
        path = Path(first.json()["path"])
        assert path == tmp_path / "esphome" / "kitchen-panel.yaml"
        assert "!secret maverick_authorization" in path.read_text()
        assert first.json()["missing_secrets"] is None, "no secrets.yaml there yet"

        path.write_text("# mine\n")
        second = client.post(
            "/api/displays/kitchen/esphome/install",
            json={"destination": "configured"}, headers=headers,
        )
        assert second.status_code == 409
        assert path.read_text() == "# mine\n"

        third = client.post(
            "/api/displays/kitchen/esphome/install",
            json={"destination": "configured", "overwrite": True}, headers=headers,
        )
        assert third.status_code == 200
        assert "esphome:" in path.read_text()

        unknown = client.post(
            "/api/displays/kitchen/esphome/install",
            json={"destination": "nowhere"}, headers=headers,
        )
        assert unknown.status_code == 400


def test_the_install_route_is_behind_the_token(app: Application) -> None:
    with TestClient(create_app(app)) as client:
        response = client.post(
            "/api/displays/kitchen/esphome/install", json={"destination": "configured"}
        )
    assert response.status_code == 401


def test_the_display_summary_says_whether_the_install_step_applies(app: Application) -> None:
    with TestClient(create_app(app)) as client:
        displays = client.get(
            "/api/displays", headers={"Authorization": f"Bearer {_TOKEN}"}
        ).json()
    applicable = {d["id"]: d["esphome_applicable"] for d in displays}
    assert applicable == {
        "kitchen": True, "colour": True, "reader": False, "tag": False, "big": True,
    }
