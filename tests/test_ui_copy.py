"""Every setting the drawer can draw has plain-language copy, and it is short.

`src/maverick/server/copy.py` is what stops the Edit drawer showing a field
under its snake_case name with a reference paragraph beneath it. A field added
to a model without an entry there would do exactly that, so the whole of
`DisplayConfig` is walked here and matched against the copy; and the help is
held to one short sentence, because a limit nobody enforces is a limit that
creeps.
"""

from __future__ import annotations

from typing import get_args

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from maverick.app import Application
from maverick.config import Config, DisplayConfig
from maverick.server import copy
from maverick.server.api import create_app
from maverick.transports import available_transports

HELP_LIMIT = 110
LABEL_LIMIT = 32


def _paths(model: type[BaseModel], prefix: str = "") -> list[str]:
    found: list[str] = []
    for name, field in model.model_fields.items():
        nested = next(
            (
                cand
                for cand in [field.annotation, *get_args(field.annotation)]
                if isinstance(cand, type) and issubclass(cand, BaseModel)
            ),
            None,
        )
        path = f"{prefix}{name}"
        # `pages` is a list of models with a section of its own, and the
        # transport's options are not in the schema at all.
        if nested is not None and name not in ("pages", "transport"):
            found.extend(_paths(nested, path + "."))
        elif name == "transport":
            found.append("transport.type")
        else:
            found.append(path)
    return found


def test_every_display_field_has_copy() -> None:
    missing = [path for path in _paths(DisplayConfig) if path not in copy.FIELDS]
    assert not missing, f"add plain-language copy for {missing} in src/maverick/server/copy.py"


def test_copy_names_no_field_that_does_not_exist() -> None:
    paths = set(_paths(DisplayConfig))
    stale = [path for path in copy.FIELDS if path not in paths]
    assert not stale, f"copy for fields that no longer exist: {stale}"


@pytest.mark.parametrize("path", sorted(copy.FIELDS))
def test_copy_is_short_and_plain(path: str) -> None:
    entry = copy.FIELDS[path]
    assert entry["label"] and len(entry["label"]) <= LABEL_LIMIT, path
    assert len(entry["help"]) <= HELP_LIMIT, f"{path}: help is {len(entry['help'])} characters"
    assert "`" not in entry["help"] and "`" not in entry["label"], f"{path}: no code in copy"
    assert entry["label"] != path.split(".")[-1] or "_" not in entry["label"], (
        f"{path}: the label is the snake_case key"
    )


def test_every_section_the_editor_draws_has_a_title() -> None:
    sections = {
        name
        for name, field in DisplayConfig.model_fields.items()
        if any(
            isinstance(cand, type) and issubclass(cand, BaseModel)
            for cand in [field.annotation, *get_args(field.annotation)]
        )
        and name != "pages"
    }
    missing = sections - set(copy.SECTIONS)
    assert not missing, f"sections without copy: {missing}"
    for key, entry in copy.SECTIONS.items():
        assert entry["title"], key
        assert len(entry.get("help", "")) <= HELP_LIMIT, key


def test_every_transport_option_has_help_or_a_documented_fallback() -> None:
    for name, cls in available_transports().items():
        if name == "fake":
            continue
        entries = copy.TRANSPORT_OPTIONS.get(name, {})
        for option in cls.options_doc:
            help_text = entries.get(option)
            assert help_text is not None, f"{name}.{option} has no plain help"
            assert len(help_text) <= HELP_LIMIT, f"{name}.{option}"
        stale = set(entries) - set(cls.options_doc)
        assert not stale, f"{name}: copy for options it does not have: {stale}"


def test_the_schema_route_serves_the_copy(tmp_path, monkeypatch) -> None:
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(Application, "start", _noop)
    monkeypatch.setattr(Application, "stop", _noop)
    config = Config.model_validate({"data_dir": str(tmp_path / "data")})
    with TestClient(create_app(Application(config))) as client:
        body = client.get("/api/schema/display").json()
    assert body["ui"]["fields"]["theme.body_mm"]["label"] == "Text size (mm)"
    assert body["ui"]["sections"]["render"]["expert"] is True
    assert body["ui"]["transports"]["opendisplay"]["device_id"]
