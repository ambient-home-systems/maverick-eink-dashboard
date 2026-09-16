"""The Home Assistant app under ``app/`` must stay consistent with the package.

Nothing here builds the image — CI does that on amd64 — but the pieces that can
drift silently are checked: the options the service reads are the ones the
schema declares, every variable the starter config substitutes is one the
service sets, the starter config loads through the real config loader, the app
version is the package version, and the package at ``MAVERICK_REF`` — which is
what the image installs, not the working tree — accepts the starter config this
commit ships.

The options half of that used to be a contract on ``run.sh``, checked by
re-reading the shell script and by running its helper under stubbed bashio
functions. The service reads ``/data/options.json`` itself now
(``src/maverick/ha/options.py``), so the same questions are asked of the module:
which keys it reads, what it treats as unset, and what it derives when an option
is empty. The Supervisor is never called — every test that needs an answer from
it provides one through the ``supervisor`` fixture — so this file opens no
socket.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from maverick.cli import _load, build_parser
from maverick.config import load_config
from maverick.ha import options as options_module
from maverick.ha.options import (
    DERIVED_VARIABLES,
    OPTION_VARIABLES,
    apply_app_options,
    load_app_options,
)
from maverick.ha.supervisor import SupervisorError

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
RUN_SH = (APP / "run.sh").read_text(encoding="utf-8")
TEMPLATE = APP / "rootfs" / "usr" / "share" / "maverick" / "maverick.yaml"

#: What the module sets for an app nobody has configured yet — every option
#: unset, no Supervisor answering. It is the state a fresh install is in, and
#: the table the starter config's ``${VAR:-default}`` fallbacks are written
#: against, so several tests below share it.
FIRST_START = {
    "HA_URL": "http://homeassistant:8123",  # config.yaml ships this default
    "HA_TOKEN": "",
    "HA_REFRESH_TOKEN": "",
    "HA_CLIENT_ID": "",
    "MAVERICK_LOG_LEVEL": "info",  # ditto
    "MAVERICK_API_TOKEN": "",
    "MAVERICK_BASE_URL": "",
    "MQTT_ENABLED": "false",  # always one of true/false, never empty
    "MQTT_HOST": "core-mosquitto",
    "MQTT_PORT": "1883",
    "MQTT_USERNAME": "",
    "MQTT_PASSWORD": "",
}


def _manifest() -> dict:
    return yaml.safe_load((APP / "config.yaml").read_text(encoding="utf-8"))


def _project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _variables() -> set[str]:
    """Every environment variable the app's options become."""
    return set(OPTION_VARIABLES.values()) | set(DERIVED_VARIABLES)


def _options_file(tmp_path: Path, **options: Any) -> Path:
    """An ``options.json`` as the Supervisor writes one into ``/data``."""
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options), encoding="utf-8")
    return path


@pytest.fixture
def supervisor(monkeypatch) -> dict[str, Any]:
    """Answer the Supervisor's read-only endpoints from a mapping.

    A path the test has not filled in raises ``SupervisorError``, which is what
    the real Supervisor produces for ``/services/mqtt`` whenever no app
    provides the service — ``400 Service not enabled``
    (``supervisor/api/services.py``, ``get_service``) — and so is the ordinary
    "Mosquitto is not installed" case rather than an exotic one. An empty
    mapping is therefore a host with no broker and no readable address.
    """
    answers: dict[str, Any] = {}

    async def fake_get(path: str) -> Any:
        if path not in answers:
            raise SupervisorError(f"Supervisor GET {path} failed (400): Service not enabled")
        return answers[path]

    monkeypatch.setattr(options_module, "api_get", fake_get)
    return answers


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


def test_run_sh_invokes_the_cli_the_way_the_cli_parses() -> None:
    """``run.sh`` is the app's entrypoint, and CI never executes it.

    The image build, the Chromium smoke test and the starter-config load all
    exercise the package; nothing runs the shell script that starts it, because
    that needs bashio and a Supervisor to answer. So the one thing a typo there
    costs — the app exiting immediately, on every start, for everyone — is
    exactly what no other check can see.

    ``-c`` is a *global* option (``build_parser`` in ``src/maverick/cli.py``,
    and "Given before the subcommand" in ``docs/reference/cli.md``), so
    ``maverick serve -c file`` is an argparse error with exit status 2 and no
    output but a usage message. Feed every ``maverick`` line in ``run.sh`` to
    the real parser instead of trusting it by eye.
    """
    parser = build_parser()
    invocations = re.findall(r"^(?:exec )?maverick (.+)$", RUN_SH, re.M)
    assert invocations, "run.sh never invokes maverick"

    for argument_string in invocations:
        # ${CONFIG_FILE} and friends stand in for a path argparse never opens.
        argv = shlex.split(re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", "/config/maverick.yaml",
                                argument_string))
        try:
            parser.parse_args(argv)
        except SystemExit as exit_:  # argparse exits rather than raising
            pytest.fail(
                f"run.sh runs `maverick {argument_string}`, which the CLI rejects "
                f"(exit {exit_.code}). The app would die on every start."
            )


def test_run_sh_leaves_the_options_to_the_service() -> None:
    """Reading the options in two places is how the two readings drift apart.

    ``run.sh`` translated them until 0.2.7, and three of the seven fixes that
    release needed were bashio's semantics rather than Maverick's (see
    ``CHANGELOG.md`` and the module docstring of
    ``src/maverick/ha/options.py``). The script keeps the container's own two
    jobs — put a config file where the user can edit it, start the service —
    and reads no options at all.
    """
    assert "bashio::config" not in RUN_SH, (
        "run.sh reads an option again; the service reads /data/options.json itself"
    )
    assert "\nexport " not in RUN_SH, (
        "run.sh exports a variable again; load_app_options sets them into os.environ"
    )


def test_the_module_reads_exactly_the_options_the_schema_declares() -> None:
    """The two halves of one contract, and nothing checks it but this.

    A key in the schema that the module never reads is an option the
    Configuration tab offers and the service ignores; a key the module reads
    that the schema does not declare is an option the Supervisor will never
    write, so it is permanently unset. Both are silent.
    """
    assert set(OPTION_VARIABLES) == set(_manifest()["schema"])


def test_every_option_the_module_reads_is_translated() -> None:
    translations = yaml.safe_load((APP / "translations" / "en.yaml").read_text(encoding="utf-8"))
    assert set(OPTION_VARIABLES) <= set(translations["configuration"])


def test_template_variables_are_set_by_the_options_module() -> None:
    """Every ``${VAR}`` in the starter config is one the service sets.

    ``${HA_URL}`` carries no ``:-`` default, so a variable that stopped being
    set would fail the load outright (``expand_env`` in
    ``src/maverick/config.py``); the rest would silently take their written
    fallback instead of the user's option.
    """
    # Comment lines are skipped: the header explains `${VAR}` substitution in words.
    body = "\n".join(
        line for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    referenced = set(re.findall(r"\$\{([A-Z_]+)", body))
    assert referenced, "the starter config substitutes nothing"
    assert referenced <= _variables()


@pytest.mark.parametrize("spelling", ["missing", "null", "empty"])
def test_missing_null_and_an_empty_string_all_mean_unset(
    tmp_path, supervisor, spelling: str
) -> None:
    """The three spellings the Supervisor produces for "not filled in".

    A key with no default in ``app/config.yaml`` is absent until it is set; the
    setup UI clears one by writing JSON ``null`` through
    ``/addons/self/options`` (``save_options`` in
    ``src/maverick/ha/supervisor.py``); a field emptied by hand is ``""``. The
    fix 0.2.4 shipped was exactly this question answered wrongly one level
    down: ``bashio::config key ''`` returns the *string* ``"null"``, which
    passes every ``${VAR:-default}`` in the config and is stored as a value —
    ``client_id: "null"`` looks like a linked account and every render then
    fails with ``Invalid client id``.
    """
    schema = _manifest()["schema"]
    if spelling == "missing":
        options: dict[str, Any] = {}
    elif spelling == "null":
        options = dict.fromkeys(schema, None)
    else:
        options = dict.fromkeys(schema, "")

    assert load_app_options(_options_file(tmp_path, **options)) == FIRST_START


def test_an_unreadable_options_file_leaves_every_option_unset(tmp_path, supervisor, caplog) -> None:
    """Refusing to start would leave the user with nothing but a log line.

    The service can be configured from its own web UI, so it has to be running
    to be fixed — the same reason a missing credential is a warning below.
    """
    with caplog.at_level(logging.WARNING, logger="maverick.ha.options"):
        values = load_app_options(tmp_path / "there-is-no-such-file.json")

    assert values == FIRST_START
    assert "Could not read the app's options" in caplog.text


def test_every_option_reaches_its_variable(tmp_path, supervisor) -> None:
    """One filled-in option per schema key, end to end.

    ``mqtt_port`` is an integer in ``options.json`` — the schema's ``port?``
    type — and the config file substitutes text, so the conversion happens here
    or nowhere.
    """
    options = {
        "home_assistant_url": "https://ha.example.com",
        "home_assistant_token": "a-long-lived-token",
        "home_assistant_refresh_token": "a-refresh-token",
        "home_assistant_client_id": "http://192.168.1.10:5000/",
        "log_level": "debug",
        "api_token": "an-api-token",
        "base_url": "http://192.168.1.10:5000",
        "mqtt_host": "broker.lan",
        "mqtt_port": 8883,
        "mqtt_username": "maverick",
        "mqtt_password": "a-secret",
    }
    assert set(options) == set(OPTION_VARIABLES), "one value per schema key, or this proves less"

    assert load_app_options(_options_file(tmp_path, **options)) == {
        "HA_URL": "https://ha.example.com",
        "HA_TOKEN": "a-long-lived-token",
        "HA_REFRESH_TOKEN": "a-refresh-token",
        "HA_CLIENT_ID": "http://192.168.1.10:5000/",
        "MAVERICK_LOG_LEVEL": "debug",
        "MAVERICK_API_TOKEN": "an-api-token",
        "MAVERICK_BASE_URL": "http://192.168.1.10:5000",
        "MQTT_ENABLED": "true",
        "MQTT_HOST": "broker.lan",
        "MQTT_PORT": "8883",
        "MQTT_USERNAME": "maverick",
        "MQTT_PASSWORD": "a-secret",
    }


def test_no_credential_is_a_warning_and_not_a_refusal(tmp_path, supervisor, caplog) -> None:
    """Starting without one is deliberate: linking happens in the web UI.

    The UI has to be running to be reached, so refusing to start would make the
    supported way of configuring the app unreachable.
    """
    with caplog.at_level(logging.WARNING, logger="maverick.ha.options"):
        values = load_app_options(_options_file(tmp_path))
    assert values["HA_URL"] == "http://homeassistant:8123"
    assert "No Home Assistant credential yet" in caplog.text

    # A refresh token alone is a credential: that is what linking writes, and
    # `build_token_source` (`src/maverick/ha/auth.py`) accepts it in its own
    # right, so warning then would be telling the user to redo what they did.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="maverick.ha.options"):
        load_app_options(
            _options_file(
                tmp_path,
                home_assistant_refresh_token="a-refresh-token",
                home_assistant_client_id="http://192.168.1.10:5000/",
            )
        )
    assert "No Home Assistant credential yet" not in caplog.text


def test_base_url_is_derived_from_the_hosts_first_ipv4_address(tmp_path, supervisor) -> None:
    """What the option's own description promises when it is left empty.

    The Supervisor reports each address with its prefix length, as
    ``address.with_prefixlen`` (``supervisor/api/network.py``,
    ``ip4config_struct``), hence the strip. The app network's own
    ``172.30.32.0/23`` is in the same payload under ``docker`` and is not a
    candidate: no panel on the LAN can reach it.
    """
    supervisor["/network/info"] = {
        "interfaces": [
            {"interface": "eth0", "enabled": False, "ipv4": None},
            {
                "interface": "wlan0",
                "primary": True,
                "ipv4": {"address": ["192.168.1.10/24", "10.9.9.9/8"], "gateway": "192.168.1.1"},
            },
        ],
        "docker": {"interface": "hassio", "address": "172.30.32.0/23"},
    }

    values = load_app_options(_options_file(tmp_path))

    assert values["MAVERICK_BASE_URL"] == "http://192.168.1.10:5000"


def test_an_explicit_base_url_is_never_second_guessed(tmp_path, supervisor) -> None:
    """The option exists for the host whose first address is the wrong one."""
    supervisor["/network/info"] = {
        "interfaces": [{"interface": "eth0", "ipv4": {"address": ["192.168.1.10/24"]}}]
    }

    values = load_app_options(_options_file(tmp_path, base_url="http://panels.example.com:5000"))

    assert values["MAVERICK_BASE_URL"] == "http://panels.example.com:5000"


def test_base_url_stays_empty_when_the_host_address_cannot_be_read(
    tmp_path, supervisor, caplog
) -> None:
    """Empty rather than wrong: `${MAVERICK_BASE_URL:-}` then leaves it unset.

    A guess would be worse than nothing — a panel told to fetch from an address
    that is not the host's simply never refreshes again, and nothing says why.
    """
    with caplog.at_level(logging.WARNING, logger="maverick.ha.options"):
        values = load_app_options(_options_file(tmp_path))

    assert values["MAVERICK_BASE_URL"] == ""
    assert "base_url is not set and the host address could not be read" in caplog.text
    assert "panels that pull frames will not know where to fetch from" in caplog.text


def test_an_explicit_broker_wins_over_the_mosquitto_app(tmp_path, supervisor) -> None:
    supervisor["/services/mqtt"] = {
        "host": "core-mosquitto",
        "port": 1883,
        "username": "addons",
        "password": "from-mosquitto",
    }

    values = load_app_options(
        _options_file(
            tmp_path, mqtt_host="broker.lan", mqtt_username="maverick", mqtt_password="a-secret"
        )
    )

    assert values["MQTT_ENABLED"] == "true"
    assert values["MQTT_HOST"] == "broker.lan"
    # The option's own default, not the Mosquitto app's port: a broker named by
    # hand is a different broker, and borrowing one value from the other's
    # credentials is how a connection fails in a way nobody can read.
    assert values["MQTT_PORT"] == "1883"
    assert values["MQTT_USERNAME"] == "maverick"
    assert values["MQTT_PASSWORD"] == "a-secret"


def test_the_mosquitto_app_is_used_when_no_broker_is_configured(tmp_path, supervisor) -> None:
    """The hand-off the ``services: mqtt:want`` declaration exists for."""
    supervisor["/services/mqtt"] = {
        "host": "core-mosquitto",
        "port": 1883,
        "ssl": False,
        "username": "addons",
        "password": "from-mosquitto",
        "protocol": "3.1.1",
    }

    values = load_app_options(_options_file(tmp_path))

    assert values["MQTT_ENABLED"] == "true"
    assert values["MQTT_HOST"] == "core-mosquitto"
    assert values["MQTT_PORT"] == "1883"
    assert values["MQTT_USERNAME"] == "addons"
    assert values["MQTT_PASSWORD"] == "from-mosquitto"


def test_no_broker_at_all_is_allowed_and_says_so(tmp_path, supervisor, caplog) -> None:
    """Displays simply do not appear as Home Assistant devices; rendering works."""
    with caplog.at_level(logging.INFO, logger="maverick.ha.options"):
        values = load_app_options(_options_file(tmp_path))

    assert values["MQTT_ENABLED"] == "false"
    assert values["MQTT_HOST"] == "core-mosquitto"
    assert "the Mosquitto broker app is not installed" in caplog.text
    assert "Set mqtt_host to change that." in caplog.text


def test_applying_the_options_is_what_the_config_file_reads(
    tmp_path, supervisor, monkeypatch
) -> None:
    """The join between the two halves: ``os.environ`` and ``${VAR}``.

    ``monkeypatch.setenv`` first for every variable, so that whatever
    ``apply_app_options`` writes directly into ``os.environ`` is restored when
    the test ends.
    """
    for name in _variables():
        monkeypatch.setenv(name, "leftover")

    applied = apply_app_options(
        _options_file(
            tmp_path,
            home_assistant_token="from-the-options-tab",
            base_url="http://192.168.1.10:5000",
        )
    )

    assert os.environ["HA_TOKEN"] == "from-the-options-tab"
    assert applied["HA_TOKEN"] == "from-the-options-tab"
    config = load_config(TEMPLATE, use_display_store=False)
    assert config.home_assistant.token == "from-the-options-tab"
    assert config.server.base_url == "http://192.168.1.10:5000"
    assert config.mqtt.enabled is False


def test_the_cli_reads_the_options_before_it_loads_the_config(
    tmp_path, supervisor, monkeypatch
) -> None:
    """The whole path, as the app's entrypoint reaches it.

    ``run.sh`` runs ``maverick -c /config/maverick.yaml serve``, and nothing
    between the Configuration tab and the loaded config runs except ``_load``
    (``src/maverick/cli.py``). It reads the options only under the Supervisor —
    a ``SUPERVISOR_TOKEN`` in the environment (``running_under_supervisor``) —
    so a bare ``maverick serve`` on a laptop that happens to have a
    ``/data/options.json`` is unaffected.
    """
    options = _options_file(
        tmp_path, home_assistant_token="from-the-options-tab", log_level="debug"
    )
    config_file = tmp_path / "maverick.yaml"
    config_file.write_text(
        "home_assistant:\n"
        "  url: ${HA_URL}\n"
        "  token: ${HA_TOKEN:-}\n"
        f"data_dir: {tmp_path / 'data'}\n"
        "log_level: ${MAVERICK_LOG_LEVEL:-info}\n",
        encoding="utf-8",
    )
    for name in _variables():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "a-supervisor-token")
    monkeypatch.setattr(options_module, "DEFAULT_OPTIONS_PATH", options)

    args = build_parser().parse_args(["-c", str(config_file), "serve"])
    try:
        config = _load(args)
    finally:
        for name in _variables():
            os.environ.pop(name, None)

    assert config.home_assistant.url == "http://homeassistant:8123"
    assert config.home_assistant.token == "from-the-options-tab"
    assert config.log_level == "debug"


def test_the_manifest_can_reach_the_api_base_url_is_derived_from() -> None:
    """`base_url`'s own description promises a fallback the Supervisor gates.

    Left empty, the service derives it from ``GET /network/info``
    (``_host_ipv4`` in ``src/maverick/ha/options.py``). An app is allowed that
    call by its *role*: the default role reaches ``^/.+/info$`` and nothing
    else (``supervisor/api/middleware/security.py``,
    ``_V1_PATTERNS.role_access[ROLE_DEFAULT]``), and the role is consulted only
    because the app asked for ``hassio_api``. The ``api_bypass`` list in the
    same file covers ``/addons/self/...``, which is why writing the app's own
    options needs no permission, but not ``/network/...`` — so without
    ``hassio_api`` the call is refused and every app left on the default
    `base_url` gets none. The setup UI then refuses to offer *Link with Home
    Assistant*, because Home Assistant has nowhere to redirect back to.
    """
    manifest = _manifest()
    assert manifest.get("hassio_api") is True, (
        "the service reads the host address from the Supervisor to fill base_url; "
        "without hassio_api that request is refused and the link cannot start"
    )
    # The default role reaches `/.+/info` and nothing more, which is all this
    # needs — so a raised role would be permission nobody asked for.
    assert "hassio_role" not in manifest, "the default role already covers /network/info"

    translated = yaml.safe_load(
        (APP / "translations" / "en.yaml").read_text(encoding="utf-8")
    )["configuration"]["base_url"]["description"]
    assert "IPv4" in translated or "ipv4" in translated, (
        "base_url's description no longer promises the host-address fallback; "
        "if the promise is gone, this permission may be too"
    )


def test_the_manifest_declares_the_service_the_broker_hand_off_needs() -> None:
    """``/services/mqtt`` is granted by the declaration, not by ``hassio_api``.

    ``/services.*`` is on the ``api_bypass`` list
    (``supervisor/api/middleware/security.py``), which is tested before the
    role, so no role would help. What decides is ``_check_access`` in
    ``supervisor/api/services.py``: it answers ``403 No access to mqtt
    service!`` unless the calling app declared the service. Dropping
    ``mqtt:want`` would leave the Mosquitto hand-off silently refused — and
    refused looks exactly like "not installed" to ``_mqtt``, which would then
    disable MQTT on a machine that has a broker.
    """
    assert "mqtt:want" in _manifest()["services"]


@pytest.mark.parametrize("mqtt", ["true", "false"])
def test_starter_config_loads(monkeypatch, mqtt: str) -> None:
    """The starter config as the package will read it, minus the store step.

    ``use_display_store=False`` here and below: this config's ``data_dir`` is
    the app's ``/config/data``, and the store step would import the example
    display into it — creating a directory on whatever machine ran the suite
    (``resolve_displays`` in ``src/maverick/store.py``). Where that store lands
    under the app is asserted separately below; the store's own behaviour is
    ``tests/test_display_store.py``.
    """
    values = {
        "HA_URL": "http://homeassistant:8123",
        "HA_TOKEN": "test-token",
        "HA_REFRESH_TOKEN": "",
        "HA_CLIENT_ID": "",
        "MAVERICK_LOG_LEVEL": "debug",
        "MAVERICK_API_TOKEN": "",
        "MAVERICK_BASE_URL": "http://192.168.1.10:5000",
        "MQTT_ENABLED": mqtt,
        "MQTT_HOST": "core-mosquitto",
        "MQTT_PORT": "1883",
        "MQTT_USERNAME": "addons",
        "MQTT_PASSWORD": "secret",
    }
    assert set(values) == _variables(), "keep this table in step with load_app_options"
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    config = load_config(TEMPLATE, use_display_store=False)

    assert config.home_assistant.token == "test-token"
    assert config.mqtt.enabled is (mqtt == "true")
    assert config.mqtt.port == 1883
    assert config.server.base_url == "http://192.168.1.10:5000"
    assert config.server.api_token == ""
    assert config.data_dir == "/config/data"
    assert config.log_level == "debug"
    assert [d.id for d in config.displays] == ["kitchen"]


def test_starter_config_loads_with_no_credential_yet(monkeypatch) -> None:
    """The state an app is in the moment it is installed, before anything is set.

    The service warns rather than refusing to start without a credential,
    because linking happens in the web UI and the UI has to be running to be
    reached. That is only true if the starter config loads with the credential,
    base URL and MQTT substitutions empty — which is exactly what
    ``load_app_options`` returns for options that carry no default in
    ``config.yaml``, and what the host address lookup leaves behind when it
    cannot read one.
    """
    assert set(FIRST_START) == _variables(), "keep this table in step with load_app_options"
    for name, value in FIRST_START.items():
        monkeypatch.setenv(name, value)

    config = load_config(TEMPLATE, use_display_store=False)

    assert config.home_assistant.token == ""
    assert config.home_assistant.refresh_token == ""
    assert config.home_assistant.client_id == ""
    assert config.mqtt.enabled is False
    assert config.server.base_url == ""
    assert config.server.api_token == ""


def test_the_display_store_lands_beside_the_frames(monkeypatch) -> None:
    """The app writes three things under ``/config/data``, and this is the third.

    Frames and ``state.json`` have always gone to ``data_dir``
    (``src/maverick/engine.py``); the displays the setup UI manages now go
    beside them, because ``displays_file`` is empty in the starter config and
    empty means ``<data_dir>/displays.yaml`` (``Config.display_store_path`` in
    ``src/maverick/config.py``). That folder is the one the Supervisor exposes
    as the app's ``addon_configs`` share, so the file can be read, backed up and
    edited like the config next to it.
    """
    for name in _variables():
        monkeypatch.setenv(name, "false" if name == "MQTT_ENABLED" else "")
    monkeypatch.setenv("HA_URL", "http://homeassistant:8123")

    config = load_config(TEMPLATE, use_display_store=False)

    assert config.displays_file == ""
    assert config.display_store_path == Path("/config/data/displays.yaml")
    # A fresh install still ships an example display to import into it.
    assert [d.id for d in config.displays] == ["kitchen"]


def test_the_starter_config_says_where_displays_live_after_the_first_start() -> None:
    """The header is the only documentation a user editing that file has.

    "Restart the app after editing" stops being the whole story for `displays:`
    the moment the first start imports the list into the store beside it, so the
    header has to say where they went (`resolve_displays` in
    ``src/maverick/store.py``).
    """
    header = "\n".join(
        line for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("#")
    )
    assert "data/displays.yaml" in header
    assert "Web UI" in header or "web UI" in header


def test_dockerfile_pins_a_ref_and_uses_the_distro_chromium() -> None:
    dockerfile = (APP / "Dockerfile").read_text(encoding="utf-8")
    pinned = re.search(r"^ARG MAVERICK_REF=([0-9a-f]{40}|v\d+\.\d+\.\d+)$", dockerfile, re.M)
    assert pinned, "MAVERICK_REF must be a full commit SHA or a vX.Y.Z tag"
    assert "MAVERICK_CHROMIUM_PATH=/usr/bin/chromium" in dockerfile


def _pinned_ref() -> str:
    dockerfile = (APP / "Dockerfile").read_text(encoding="utf-8")
    pinned = re.search(r"^ARG MAVERICK_REF=(\S+)$", dockerfile, re.M)
    assert pinned, "MAVERICK_REF is not pinned"
    return pinned.group(1)


def test_pinned_ref_accepts_the_starter_config(tmp_path) -> None:
    """The pinned package and the starter config reach a user by different routes.

    ``app/rootfs/usr/share/maverick/maverick.yaml`` is copied out of the image
    at whatever commit the store built; the package inside that image comes
    from ``MAVERICK_REF``. Every model bar ``TransportConfig`` forbids unknown
    keys (``src/maverick/config.py:71-77``), so a ref left behind at an older
    release writes keys that release rejects, ``maverick serve`` exits on a
    validation error, and the app never starts — before its web UI, and so
    before *Link with Home Assistant*, can be reached. 0.2.0 shipped exactly
    that, with ``MAVERICK_REF`` on a commit predating
    ``home_assistant.refresh_token``.

    So load today's starter config with the pinned commit's own config module,
    which is what the image will do on a user's first start. Comparing version
    numbers instead would fail the release commit that bumps
    ``app/config.yaml`` before the ref can move (see *Releasing* in
    CONTRIBUTING.md); this asks the question that actually matters.
    """
    ref = _pinned_ref()
    archive = tmp_path / "pinned.tar"
    try:
        subprocess.run(
            ["git", "archive", "--format=tar", "--output", str(archive), ref, "src"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        # A shallow clone has the working tree but not the pinned commit. CI's
        # `app` job covers the same ground from the other side, loading the
        # starter config inside the image it just built.
        pytest.skip(f"MAVERICK_REF {ref} is not in this checkout: {error}")

    with tarfile.open(archive) as tar:
        tar.extractall(tmp_path, filter="data")
    pinned_src = tmp_path / "src"

    program = (
        "import sys\n"
        "import maverick\n"
        "from maverick.config import load_config\n"
        "print(maverick.__file__)\n"
        "load_config(sys.argv[1])\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program, str(TEMPLATE)],
        capture_output=True,
        text=True,
        # What the options module sets on a first start, per the table above.
        env={**os.environ, "PYTHONPATH": str(pinned_src), **FIRST_START},
    )

    imported = result.stdout.splitlines()[0] if result.stdout else ""
    if not imported.startswith(str(pinned_src)):
        # An editable install that hooks sys.meta_path outranks PYTHONPATH, and
        # then this would be testing the working tree against itself.
        pytest.skip(f"the pinned tree was shadowed by {imported or 'an unknown maverick'}")

    assert result.returncode == 0, (
        f"the package at MAVERICK_REF {ref} rejects the starter configuration this "
        f"commit ships, so the app would fail its first start:\n{result.stderr}"
    )
