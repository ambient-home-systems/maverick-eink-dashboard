"""The root Dockerfile and docker-compose.dev.yml, checked without Docker.

Nothing here builds an image or runs `docker compose` — CI's `image` job does
that (`.github/workflows/ci.yml`). This only checks the things a reviewer
would otherwise have to read the files to confirm: that the standalone image
installs from the build context rather than a URL (unlike `app/Dockerfile`,
which installs from a pinned git ref — `tests/test_app.py` covers that one),
that it points at the distro Chromium, that it declares the three volumes the
README documents, and that the dev compose file parses and names the three
services CONTRIBUTING.md describes.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
# Comments explain the deliberate differences from app/Dockerfile, which
# installs from a pinned ref behind a Supervisor and so mentions exactly the
# terms this file's instructions must not use — read only the instructions.
DOCKERFILE_INSTRUCTIONS = "\n".join(
    line for line in DOCKERFILE.splitlines() if not line.lstrip().startswith("#")
)


def test_installs_from_the_build_context_not_a_url() -> None:
    assert "COPY . /src" in DOCKERFILE_INSTRUCTIONS
    assert "pip install /src" in DOCKERFILE_INSTRUCTIONS
    assert "archive/" not in DOCKERFILE_INSTRUCTIONS
    assert "MAVERICK_REF" not in DOCKERFILE_INSTRUCTIONS


def test_sets_the_chromium_path() -> None:
    assert "MAVERICK_CHROMIUM_PATH=/usr/bin/chromium" in DOCKERFILE


def test_installs_the_distro_chromium_and_the_three_fonts() -> None:
    assert "chromium" in DOCKERFILE
    for font_package in ("fonts-dejavu-core", "fonts-liberation", "fonts-noto-core"):
        assert font_package in DOCKERFILE


def test_declares_the_three_volumes() -> None:
    assert 'VOLUME ["/config", "/media", "/share"]' in DOCKERFILE


def test_runs_serve_against_a_mounted_config() -> None:
    assert 'CMD ["maverick", "-c", "/config/maverick.yaml", "serve"' in DOCKERFILE


def test_does_not_assume_a_supervisor() -> None:
    assert "options.json" not in DOCKERFILE_INSTRUCTIONS
    assert "bashio" not in DOCKERFILE_INSTRUCTIONS


def test_healthcheck_matches_the_app_image() -> None:
    app_dockerfile = (ROOT / "app" / "Dockerfile").read_text(encoding="utf-8")
    app_healthcheck = next(
        line for line in app_dockerfile.splitlines() if line.startswith("HEALTHCHECK")
    )
    root_healthcheck = next(
        line for line in DOCKERFILE.splitlines() if line.startswith("HEALTHCHECK")
    )
    assert root_healthcheck == app_healthcheck


def _compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8"))


def test_compose_file_parses_as_yaml() -> None:
    compose = _compose()
    assert isinstance(compose, dict)


def test_compose_names_the_three_services() -> None:
    assert set(_compose()["services"]) == {"homeassistant", "mosquitto", "maverick"}


def test_compose_homeassistant_service_is_seeded_from_dev_directory() -> None:
    homeassistant = _compose()["services"]["homeassistant"]
    assert homeassistant["image"] == "ghcr.io/home-assistant/home-assistant:stable"
    assert any(
        volume.startswith("./dev/homeassistant:") for volume in homeassistant["volumes"]
    )


def test_compose_mosquitto_service_uses_the_seeded_config() -> None:
    mosquitto = _compose()["services"]["mosquitto"]
    assert mosquitto["image"] == "eclipse-mosquitto"
    assert any("dev/mosquitto.conf" in volume for volume in mosquitto["volumes"])


def test_compose_maverick_service_builds_the_root_dockerfile() -> None:
    maverick = _compose()["services"]["maverick"]
    assert maverick["build"]["context"] == "."
    assert maverick["build"]["dockerfile"] == "Dockerfile"
    assert any("dev/maverick.yaml" in volume for volume in maverick["volumes"])


def test_dev_seed_files_exist() -> None:
    assert (ROOT / "dev" / "homeassistant" / "configuration.yaml").is_file()
    assert (ROOT / "dev" / "mosquitto.conf").is_file()
    assert (ROOT / "dev" / "maverick.yaml").is_file()
