"""The Home Assistant app under ``app/`` must stay consistent with the package.

Nothing here builds the image — CI does that on amd64 — but the pieces that can
drift silently are checked: the option keys ``run.sh`` reads exist in the
schema, every variable the starter config substitutes is exported by ``run.sh``,
the starter config loads through the real config loader, and the app version is
the package version, because the image installs the package at a pinned commit.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

from maverick.config import load_config

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
RUN_SH = (APP / "run.sh").read_text(encoding="utf-8")
TEMPLATE = APP / "rootfs" / "usr" / "share" / "maverick" / "maverick.yaml"


def _manifest() -> dict:
    return yaml.safe_load((APP / "config.yaml").read_text(encoding="utf-8"))


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _exported() -> set[str]:
    names: set[str] = set()
    for line in RUN_SH.splitlines():
        if line.startswith("export "):
            names.update(line.split()[1:])
    return names


def test_repository_manifest_points_at_this_repository() -> None:
    manifest = yaml.safe_load((ROOT / "repository.yaml").read_text(encoding="utf-8"))
    assert manifest["name"]
    assert manifest["url"] == _project()["urls"]["Homepage"]


def test_app_version_is_the_package_version() -> None:
    assert _manifest()["version"] == _project()["version"], (
        "app/config.yaml `version` must equal pyproject.toml's: the image installs the package"
    )


def test_manifest_shape() -> None:
    manifest = _manifest()
    assert manifest["slug"] == "maverick"
    assert manifest["init"] is False
    assert set(manifest["arch"]) == {"aarch64", "amd64"}
    assert set(manifest["options"]) <= set(manifest["schema"])
    # Optional keys carry no default: an empty string would fail the url/port types.
    optional = {key for key, kind in manifest["schema"].items() if str(kind).endswith("?")}
    assert not optional & set(manifest["options"])


def test_every_option_run_sh_reads_is_declared() -> None:
    read = set(re.findall(r"bashio::config(?:\.has_value)? '([a-z_]+)'", RUN_SH))
    assert read, "run.sh reads no options"
    assert read <= set(_manifest()["schema"])


def test_every_schema_key_is_translated() -> None:
    translations = yaml.safe_load((APP / "translations" / "en.yaml").read_text(encoding="utf-8"))
    assert set(_manifest()["schema"]) <= set(translations["configuration"])


def test_template_variables_are_exported_by_run_sh() -> None:
    # Comment lines are skipped: the header explains `${VAR}` substitution in words.
    body = "\n".join(
        line for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    referenced = set(re.findall(r"\$\{([A-Z_]+)", body))
    assert referenced, "the starter config substitutes nothing"
    assert referenced <= _exported()


@pytest.mark.parametrize("mqtt", ["true", "false"])
def test_starter_config_loads(monkeypatch, mqtt: str) -> None:
    values = {
        "HA_URL": "http://homeassistant:8123",
        "HA_TOKEN": "test-token",
        "MAVERICK_LOG_LEVEL": "debug",
        "MAVERICK_API_TOKEN": "",
        "MAVERICK_BASE_URL": "http://192.168.1.10:5000",
        "MQTT_ENABLED": mqtt,
        "MQTT_HOST": "core-mosquitto",
        "MQTT_PORT": "1883",
        "MQTT_USERNAME": "addons",
        "MQTT_PASSWORD": "secret",
    }
    assert set(values) == _exported(), "keep this table in step with the exports in run.sh"
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    config = load_config(TEMPLATE)

    assert config.home_assistant.token == "test-token"
    assert config.mqtt.enabled is (mqtt == "true")
    assert config.mqtt.port == 1883
    assert config.server.base_url == "http://192.168.1.10:5000"
    assert config.server.api_token == ""
    assert config.data_dir == "/config/data"
    assert config.log_level == "debug"
    assert [d.id for d in config.displays] == ["kitchen"]


def test_dockerfile_pins_a_ref_and_uses_the_distro_chromium() -> None:
    dockerfile = (APP / "Dockerfile").read_text(encoding="utf-8")
    pinned = re.search(r"^ARG MAVERICK_REF=([0-9a-f]{40}|v\d+\.\d+\.\d+)$", dockerfile, re.M)
    assert pinned, "MAVERICK_REF must be a full commit SHA or a vX.Y.Z tag"
    assert "MAVERICK_CHROMIUM_PATH=/usr/bin/chromium" in dockerfile
