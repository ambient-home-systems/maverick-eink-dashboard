"""The display store: the file Maverick writes its displays to.

`src/maverick/store.py` is the one place that decides where a display comes
from, so the things worth pinning down are the file format (short enough to
hand-edit, and every transport option intact), the precedence between the store
and the ``displays:`` list in the config file, and the fact that a broken store
fails exactly the way a broken config file does — the same validators, the same
messages, because a user meeting one of them has no reason to care which file
it came from.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from maverick.cli import main
from maverick.config import Config, ConfigError, DisplayConfig, load_config
from maverick.store import DisplayStore, dump_display

KITCHEN = {
    "id": "kitchen",
    "panel": "waveshare-7in5-mono",
    "dashboard": "/lovelace-eink/kitchen",
    "schedule": {"every": "5m", "quiet_hours": "23:00-06:30"},
    "transport": {
        "type": "opendisplay",
        "mode": "ble",
        "mac": "AA:BB:CC:DD:EE:FF",
        "retries": 3,
        "verify": False,
    },
}


def write_config(tmp_path, **extra) -> Path:
    """A config file with the data directory inside ``tmp_path``."""
    path = tmp_path / "maverick.yaml"
    path.write_text(
        yaml.safe_dump({"data_dir": str(tmp_path / "data"), **extra}), encoding="utf-8"
    )
    return path


# --------------------------------------------------------------- the file --

def test_a_stored_display_is_as_short_as_a_hand_written_one(tmp_path) -> None:
    """Only what differs from a default display, plus every transport option.

    The store is meant to be readable by the person whose config file it
    replaces, so a display that names a panel and a dashboard must not come back
    as the eighty-odd keys `DisplayConfig` actually has. The derived ones go too:
    `name` is filled in from `id` by `DisplayConfig._defaults`, and `debounce` is
    stored as the seconds its ``"10s"`` default parses to, so both would survive
    a naive comparison against their defaults.
    """
    store = DisplayStore(tmp_path / "displays.yaml")
    store.save([DisplayConfig.model_validate(KITCHEN)])

    written = yaml.safe_load(store.path.read_text(encoding="utf-8"))

    assert written["version"] == 1
    assert written["displays"] == [KITCHEN]


def test_every_transport_extra_survives_the_round_trip(tmp_path) -> None:
    """`TransportConfig` is ``extra="allow"``, and that is where the options live.

    Nothing in the model names `mac`, `mode` or `retries`
    (`src/maverick/config.py`, `TransportConfig`), so nothing but this keeps a
    dump from dropping them — and a dropped option means a panel that stops
    being delivered to.
    """
    store = DisplayStore(tmp_path / "displays.yaml")
    original = DisplayConfig.model_validate(KITCHEN)
    store.save([original])

    (restored,) = store.load()

    assert restored == original
    assert restored.transport.mac == "AA:BB:CC:DD:EE:FF"
    assert restored.transport.retries == 3
    assert restored.transport.verify is False


def test_a_display_left_at_its_defaults_stores_as_its_id(tmp_path) -> None:
    store = DisplayStore(tmp_path / "displays.yaml")
    store.save([DisplayConfig.model_validate({"id": "spare"})])

    assert dump_display(DisplayConfig.model_validate({"id": "spare"})) == {"id": "spare"}
    assert store.load()[0].id == "spare"


def test_saving_leaves_no_temporary_file_behind(tmp_path) -> None:
    """Write-then-rename, as ``FrameStore._persist`` does.

    The setup UI saves while the service is running, so a reader must never meet
    a half-written file — and the rename must not leave the half behind either,
    in a directory a user is expected to look at.
    """
    store = DisplayStore(tmp_path / "data" / "displays.yaml")
    store.save([DisplayConfig.model_validate(KITCHEN)])
    store.save([DisplayConfig.model_validate({"id": "spare"})])

    assert store.exists()
    assert sorted(p.name for p in (tmp_path / "data").iterdir()) == ["displays.yaml"]
    assert [d.id for d in store.load()] == ["spare"]


# -------------------------------------------------------- reading it back --

def test_an_environment_variable_is_expanded_on_load(tmp_path, monkeypatch) -> None:
    """A hand-edited store keeps `${VAR}`, exactly as the config file does."""
    monkeypatch.setenv("PANEL_MAC", "11:22:33:44:55:66")
    store = DisplayStore(tmp_path / "displays.yaml")
    store.path.write_text(
        "version: 1\n"
        "displays:\n"
        "  - id: kitchen\n"
        "    transport:\n"
        "      type: opendisplay\n"
        "      mac: ${PANEL_MAC}\n",
        encoding="utf-8",
    )

    (display,) = store.load()

    assert display.transport.mac == "11:22:33:44:55:66"


def test_an_unset_environment_variable_fails_the_way_the_config_file_does(tmp_path) -> None:
    store = DisplayStore(tmp_path / "displays.yaml")
    store.path.write_text(
        "version: 1\ndisplays:\n  - id: kitchen\n    dashboard: ${MAVERICK_TEST_UNSET}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="MAVERICK_TEST_UNSET is referenced in the config"):
        store.load()


def test_a_save_resolves_the_substitution_it_read(tmp_path, monkeypatch) -> None:
    """The store is machine-owned, and the module docstring says so.

    `${VAR}` is read, but what is written back is the value it expanded to, so a
    hand-written substitution survives only until the next save.
    """
    monkeypatch.setenv("PANEL_MAC", "11:22:33:44:55:66")
    store = DisplayStore(tmp_path / "displays.yaml")
    store.path.write_text(
        "version: 1\ndisplays:\n  - id: kitchen\n"
        "    transport:\n      type: opendisplay\n      mac: ${PANEL_MAC}\n",
        encoding="utf-8",
    )

    store.save(store.load())

    assert "${PANEL_MAC}" not in store.path.read_text(encoding="utf-8")
    assert store.load()[0].transport.mac == "11:22:33:44:55:66"


def test_duplicate_ids_fail_with_the_config_files_own_message(tmp_path) -> None:
    """The same error `Config._unique_ids` raises, because it is that check.

    `DisplayStore.load` validates through `Config`, so a store with two
    `kitchen` entries fails with the message the config file would have given —
    down to the wording a user searches the troubleshooting page for.
    """
    store = DisplayStore(tmp_path / "displays.yaml")
    store.save([DisplayConfig.model_validate({"id": "kitchen"})])
    store.path.write_text(
        store.path.read_text(encoding="utf-8").replace(
            "- id: kitchen", "- id: kitchen\n- id: kitchen"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as from_store:
        store.load()
    with pytest.raises(ValidationError) as from_config:
        Config.model_validate({"displays": [{"id": "kitchen"}, {"id": "kitchen"}]})

    assert "duplicate display id 'kitchen'" in str(from_store.value)
    assert from_store.value.errors()[0]["msg"] == from_config.value.errors()[0]["msg"]


def test_a_store_from_a_newer_maverick_is_refused(tmp_path) -> None:
    store = DisplayStore(tmp_path / "displays.yaml")
    store.path.write_text("version: 2\ndisplays: []\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="display store of version 2"):
        store.load()


def test_a_store_that_is_not_a_mapping_is_refused(tmp_path) -> None:
    store = DisplayStore(tmp_path / "displays.yaml")
    store.path.write_text("- id: kitchen\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="must contain a YAML mapping"):
        store.load()


# ------------------------------------------------------------ precedence --

def test_displays_are_imported_once_and_read_from_the_store_after(tmp_path, caplog) -> None:
    """The upgrade path: a config file with displays, and no store yet."""
    config_file = write_config(tmp_path, displays=[KITCHEN])
    store_path = tmp_path / "data" / "displays.yaml"

    with caplog.at_level(logging.INFO, logger="maverick.store"):
        first = load_config(config_file)

    assert store_path.exists()
    assert [d.id for d in first.displays] == ["kitchen"]
    assert "imported 1 display(s)" in caplog.text
    assert str(store_path) in caplog.text
    assert first.displays_source == f"{config_file} (imported into {store_path})"

    # A second load reads the store, and the display is unchanged by the trip.
    second = load_config(config_file)
    assert second.displays == first.displays
    assert second.displays_source == f"{store_path} (the display store)"


def test_the_store_wins_and_says_so(tmp_path, caplog) -> None:
    """Two files listing displays is the failure mode `docs/roadmap.md` names.

    The store is the one the setup UI writes, so it is the one that counts; the
    warning has to name both files, because the user has to be able to find the
    list that is being ignored.
    """
    config_file = write_config(tmp_path, displays=[KITCHEN])
    store = DisplayStore(tmp_path / "data" / "displays.yaml")
    store.save([DisplayConfig.model_validate({"id": "hallway", "panel": "generic-mono"})])

    with caplog.at_level(logging.WARNING, logger="maverick.store"):
        config = load_config(config_file)

    assert [d.id for d in config.displays] == ["hallway"]
    assert str(config_file) in caplog.text
    assert str(store.path) in caplog.text
    assert "the store wins" in caplog.text
    assert "can be deleted" in caplog.text


def test_no_displays_anywhere_writes_nothing(tmp_path) -> None:
    config_file = write_config(tmp_path)

    config = load_config(config_file)

    assert config.displays == []
    assert not (tmp_path / "data").exists()
    assert config.displays_source == str(config_file)


def test_displays_file_overrides_where_the_store_lives(tmp_path) -> None:
    elsewhere = tmp_path / "elsewhere" / "panels.yaml"
    config_file = write_config(tmp_path, displays_file=str(elsewhere), displays=[KITCHEN])

    config = load_config(config_file)

    assert config.display_store_path == elsewhere
    assert elsewhere.exists()
    assert not (tmp_path / "data" / "displays.yaml").exists()
    assert [d.id for d in load_config(config_file).displays] == ["kitchen"]


def test_a_store_that_cannot_be_written_is_not_fatal(tmp_path, caplog, monkeypatch) -> None:
    """A read-only data directory costs the setup UI, not the renders.

    The frames degrade this way too (``FrameStore._persist``): a service that
    cannot write its own state still renders and delivers, which is the part the
    panel on the wall depends on.
    """
    config_file = write_config(tmp_path, displays=[KITCHEN])

    def refuse(self, displays):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(DisplayStore, "save", refuse)

    with caplog.at_level(logging.WARNING, logger="maverick.store"):
        config = load_config(config_file)

    assert [d.id for d in config.displays] == ["kitchen"]
    assert "could not write the display store" in caplog.text
    assert config.displays_source == str(config_file)


def test_a_config_built_in_memory_never_touches_the_store(tmp_path) -> None:
    """The store step belongs to loading a file, not to the model.

    Every test in this suite builds a `Config` with `model_validate`, and none
    of them should acquire a file on disk by doing so.
    """
    config = Config.model_validate({"data_dir": str(tmp_path / "data"), "displays": [KITCHEN]})

    assert [d.id for d in config.displays] == ["kitchen"]
    assert not (tmp_path / "data").exists()
    assert config.displays_source == ""


def test_check_says_which_file_the_displays_came_from(tmp_path, capsys) -> None:
    """`maverick check` is where a user asks which of the two files is in force.

    It reaches the store through `_load` like every other subcommand, so running
    it twice is also the shortest demonstration that the import happens once.
    With no credential configured the Home Assistant check answers from the
    config alone and touches no network (`cmd_check` in `src/maverick/cli.py`).
    """
    config_file = write_config(tmp_path, displays=[{"id": "kitchen", "panel": "generic-mono"}])
    store_path = tmp_path / "data" / "displays.yaml"

    main(["-c", str(config_file), "check"])
    first = capsys.readouterr().out
    main(["-c", str(config_file), "check"])
    second = capsys.readouterr().out

    assert f"1 display(s) from {config_file} (imported into {store_path})" in first
    assert f"1 display(s) from {store_path} (the display store)" in second


def test_use_display_store_false_reads_the_file_alone(tmp_path) -> None:
    config_file = write_config(tmp_path, displays=[KITCHEN])

    config = load_config(config_file, use_display_store=False)

    assert [d.id for d in config.displays] == ["kitchen"]
    assert not (tmp_path / "data").exists()
