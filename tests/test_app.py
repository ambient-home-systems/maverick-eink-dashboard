"""The Home Assistant app under ``app/`` must stay consistent with the package.

Nothing here builds the image — CI does that on amd64 — but the pieces that can
drift silently are checked: the option keys ``run.sh`` reads exist in the
schema, every variable the starter config substitutes is exported by ``run.sh``,
the starter config loads through the real config loader, the app version is the
package version, and the package at ``MAVERICK_REF`` — which is what the image
installs, not the working tree — accepts the starter config this commit ships.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

import pytest
import yaml

from maverick.cli import build_parser
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


def test_every_option_run_sh_reads_is_declared() -> None:
    read = set(
        re.findall(r"(?:bashio::config(?:\.has_value)?|config_or_empty) '([a-z_]+)'", RUN_SH)
    )
    assert read, "run.sh reads no options"
    assert read <= set(_manifest()["schema"])


def test_an_unset_option_is_never_read_with_an_empty_bashio_default() -> None:
    """`bashio::config key ''` yields the string "null", not an empty string.

    bashio reads its own fallback as ``${2:-null}``, and ``:-`` substitutes on
    an empty argument too, so an empty default becomes the literal ``null``
    (bashio ``lib/config.sh``). Nothing downstream catches it: ``"null"`` is a
    non-empty string, so it passes every ``${VAR:-default}`` in the starter
    config and is stored as the value. The visible damage is a credential that
    does not exist — ``refresh_token`` and ``client_id`` both ``"null"`` build
    a linked account (`src/maverick/ha/auth.py`, `build_token_source`) whose
    every refresh is answered `400 Invalid client id` — plus a ``base_url``
    that blocks linking and an ``api_token`` gating the pull endpoints.
    """
    offenders = re.findall(r"bashio::config '([a-z_]+)' ''", RUN_SH)
    assert not offenders, (
        f"{offenders} are read with an empty bashio default, which yields the "
        "string 'null'. Read optional options through config_or_empty instead."
    )


def test_config_or_empty_turns_an_unset_option_into_an_empty_string() -> None:
    """The helper itself, run against bashio's real contract.

    The stubs below reproduce ``bashio::config`` and ``bashio::config.has_value``
    from bashio ``lib/config.sh``, including the ``${2:-null}`` fallback that
    causes the problem, so this fails if the helper is rewritten to call
    ``bashio::config`` with a default again.
    """
    body = re.search(r"^config_or_empty\(\) \{.*?^\}$", RUN_SH, re.M | re.S)
    assert body, "run.sh no longer defines config_or_empty"

    script = f"""
    bashio::config() {{
        local default_value=${{2:-null}}
        case "${{1}}" in
            set_option) printf '%s' 'a-value' ;;
            *) echo "${{default_value}}" ;;
        esac
    }}
    bashio::config.has_value() {{
        [[ "$(bashio::config "${{1}}")" != "null" && -n "$(bashio::config "${{1}}")" ]]
    }}
    {body.group(0)}
    printf '[%s][%s]' "$(config_or_empty 'unset_option')" "$(config_or_empty 'set_option')"
    """
    result = subprocess.run(
        ["bash", "-o", "errexit", "-o", "pipefail", "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "[][a-value]", result.stdout


def test_the_manifest_can_reach_the_api_run_sh_derives_base_url_from() -> None:
    """`base_url`'s own description promises a fallback the Supervisor gates.

    Left empty, ``run.sh`` derives it from ``bashio::network.ipv4_address``,
    which calls ``GET /network/info`` (bashio ``lib/network.sh``). The
    Supervisor's ``api_bypass`` list covers ``/addons/self/...`` — which is why
    writing the app's own options needs no permission — but not ``/network/...``,
    so without ``hassio_api`` the call is refused and every app left on the
    default `base_url` gets none. The setup UI then refuses to offer *Link with
    Home Assistant*, because Home Assistant has nowhere to redirect back to.
    """
    manifest = _manifest()
    assert manifest.get("hassio_api") is True, (
        "run.sh reads the host address from the Supervisor to fill base_url; "
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
    assert set(values) == _exported(), "keep this table in step with the exports in run.sh"
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

    ``run.sh`` warns rather than refusing to start without a credential, because
    linking happens in the web UI and the UI has to be running to be reached.
    That is only true if the starter config loads with the credential, base URL
    and MQTT substitutions empty — what ``bashio::config`` hands ``run.sh`` for
    options that carry no default in ``config.yaml``, and what the host address
    lookup leaves behind when it cannot read one.
    """
    first_start = {
        "HA_URL": "http://homeassistant:8123",  # config.yaml ships this default
        "HA_TOKEN": "",
        "HA_REFRESH_TOKEN": "",
        "HA_CLIENT_ID": "",
        "MAVERICK_LOG_LEVEL": "info",  # ditto
        "MAVERICK_API_TOKEN": "",
        "MAVERICK_BASE_URL": "",
        "MQTT_ENABLED": "false",  # run.sh always writes one of true/false
        "MQTT_HOST": "core-mosquitto",
        "MQTT_PORT": "1883",
        "MQTT_USERNAME": "",
        "MQTT_PASSWORD": "",
    }
    assert set(first_start) == _exported(), "keep this table in step with the exports in run.sh"
    for name, value in first_start.items():
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
    for name in _exported():
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
        env={
            **os.environ,
            "PYTHONPATH": str(pinned_src),
            # What run.sh exports on a first start, per the table above.
            "HA_URL": "http://homeassistant:8123",
            "HA_TOKEN": "",
            "HA_REFRESH_TOKEN": "",
            "HA_CLIENT_ID": "",
            "MAVERICK_LOG_LEVEL": "info",
            "MAVERICK_API_TOKEN": "",
            "MAVERICK_BASE_URL": "",
            "MQTT_ENABLED": "false",
            "MQTT_HOST": "core-mosquitto",
            "MQTT_PORT": "1883",
            "MQTT_USERNAME": "",
            "MQTT_PASSWORD": "",
        },
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
