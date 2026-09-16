"""``${VAR:-default}`` means what it means in a shell.

``expand_env`` (``src/maverick/config.py``) is what keeps secrets out of a file
that usually lives in a shared ``/config`` folder. The interesting case is a
variable that is *set but empty*, because that is not a corner: under the Home
Assistant app a value is set for every substitution in the starter config
(``load_app_options`` in ``src/maverick/ha/options.py``), and the options a
user has not filled in are set to the empty string. A default written next to
the reference has to survive that.
"""

from __future__ import annotations

import pytest

from maverick.config import ConfigError, expand_env, load_config


def test_default_stands_in_for_an_unset_variable(monkeypatch) -> None:
    monkeypatch.delenv("MAVERICK_TEST_VAR", raising=False)
    assert expand_env("${MAVERICK_TEST_VAR:-fallback}") == "fallback"


def test_default_stands_in_for_an_empty_variable(monkeypatch) -> None:
    """The shell's `:-`, and the reason this is not a cosmetic distinction.

    Every variable the starter config names is set unconditionally, whether or
    not the option behind it was filled in — `MAVERICK_API_TOKEN` on a fresh
    install, or `MQTT_PORT` for anyone launching the service by hand with a
    partly filled environment. Treating that as "set" would put an empty string
    where a port belongs and fail validation on a field whose fallback is
    written right there in the file.
    """
    monkeypatch.setenv("MAVERICK_TEST_VAR", "")
    assert expand_env("${MAVERICK_TEST_VAR:-1883}") == "1883"


def test_a_set_variable_wins_over_the_default(monkeypatch) -> None:
    monkeypatch.setenv("MAVERICK_TEST_VAR", "8883")
    assert expand_env("${MAVERICK_TEST_VAR:-1883}") == "8883"


def test_an_empty_default_yields_the_empty_string(monkeypatch) -> None:
    """`${VAR:-}` is how the shipped configs say "optional, usually empty"."""
    monkeypatch.setenv("MAVERICK_TEST_VAR", "")
    assert expand_env("${MAVERICK_TEST_VAR:-}") == ""
    monkeypatch.delenv("MAVERICK_TEST_VAR")
    assert expand_env("${MAVERICK_TEST_VAR:-}") == ""


def test_no_default_still_requires_the_variable_to_be_set(monkeypatch) -> None:
    monkeypatch.delenv("MAVERICK_TEST_VAR", raising=False)
    with pytest.raises(ConfigError, match="MAVERICK_TEST_VAR"):
        expand_env("${MAVERICK_TEST_VAR}")


def test_no_default_accepts_an_empty_variable(monkeypatch) -> None:
    """Set-and-empty is not the unset error: with no default there is nothing else to use."""
    monkeypatch.setenv("MAVERICK_TEST_VAR", "")
    assert expand_env("${MAVERICK_TEST_VAR}") == ""


def test_substitution_reaches_nested_values_and_leaves_keys_alone(monkeypatch) -> None:
    monkeypatch.setenv("MAVERICK_TEST_VAR", "")
    raw = {
        "${MAVERICK_TEST_VAR:-untouched}": ["${MAVERICK_TEST_VAR:-in-a-list}"],
        "nested": {"deep": "port ${MAVERICK_TEST_VAR:-1883} here"},
        "left_alone": 5000,
    }
    assert expand_env(raw) == {
        "${MAVERICK_TEST_VAR:-untouched}": ["in-a-list"],
        "nested": {"deep": "port 1883 here"},
        "left_alone": 5000,
    }


def test_an_empty_export_does_not_defeat_a_config_default(tmp_path, monkeypatch) -> None:
    """End to end, through the loader, in the shape the app's starter config uses."""
    config_file = tmp_path / "maverick.yaml"
    config_file.write_text(
        # `data_dir` is where the display store lands, and this test loads a
        # file with a display in it: point it at tmp_path so the import writes
        # there rather than into the working directory.
        f"data_dir: {tmp_path / 'data'}\n"
        "mqtt:\n"
        "  enabled: ${MAVERICK_TEST_ENABLED:-false}\n"
        "  port: ${MAVERICK_TEST_PORT:-1883}\n"
        "  password: ${MAVERICK_TEST_PASSWORD:-}\n"
        "displays:\n"
        "  - id: kitchen\n"
        "    panel: waveshare-7in5-mono\n"
        "    dashboard: /lovelace/0\n",
        encoding="utf-8",
    )
    for name in ("MAVERICK_TEST_ENABLED", "MAVERICK_TEST_PORT", "MAVERICK_TEST_PASSWORD"):
        monkeypatch.setenv(name, "")

    config = load_config(config_file)

    assert config.mqtt.enabled is False
    assert config.mqtt.port == 1883
    assert config.mqtt.password == ""
