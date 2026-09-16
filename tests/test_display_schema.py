"""What the per-display editor draws itself from.

The setup UI's drawer generates a control for every field of
:class:`~maverick.config.DisplayConfig` out of ``GET /api/schema/display``,
which is ``model_json_schema()`` plus the transport registry
(``src/maverick/server/api.py``). Nothing in ``static/app.js`` names a field:
the schema decides the control, and the field's own ``Field(description=...)``
is the help text under it. So a field that describes itself badly is a field
the editor shows badly, and there is no second copy to notice.

Three things have to hold for that to work, and each fails here rather than in
a browser:

* **every field carries a description**, because the drawer shows it as help
  and ``scripts/gen_docs.py`` builds the reference page from the same string.
  That script checks it too, over the whole of ``Config`` — but it runs as a
  separate CI step, and this is the one the editor depends on;
* **every enum the schema exposes has values**, since an enum field becomes a
  ``<select>`` and an empty one offers nothing to choose;
* **the transport map names every registered transport**, because
  ``TransportConfig`` is the one ``extra="allow"`` model
  (``src/maverick/config.py``) and its options are not in the schema at all —
  each transport's ``options_doc`` is the only description of them there is.

The browser-level test of what ``app.js`` does with the payload is P4.3 in
``docs/implementation-plan.md``.
"""

from __future__ import annotations

from typing import get_args

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from maverick.app import Application
from maverick.config import Config, DisplayConfig
from maverick.server.api import create_app
from maverick.transports import available_transports


async def _noop(*args, **kwargs) -> None:
    return None


@pytest.fixture
def schema(tmp_path, monkeypatch) -> dict:
    """``GET /api/schema/display``, as the drawer fetches it."""
    monkeypatch.setattr(Application, "start", _noop)
    monkeypatch.setattr(Application, "stop", _noop)
    config = Config.model_validate({"data_dir": str(tmp_path / "data")})
    with TestClient(create_app(Application(config))) as client:
        response = client.get("/api/schema/display")
    assert response.status_code == 200
    return response.json()


def _models(model: type[BaseModel], seen: set | None = None) -> list[type[BaseModel]]:
    """``model`` and every pydantic model reachable from its fields."""
    seen = set() if seen is None else seen
    if model in seen:
        return []
    seen.add(model)
    found = [model]
    for field in model.model_fields.values():
        for nested in _nested_models(field.annotation):
            found.extend(_models(nested, seen))
    return found


def _nested_models(annotation: object) -> list[type[BaseModel]]:
    """Every model class inside an annotation, `X | None` and `list[X]` included."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [m for arg in get_args(annotation) for m in _nested_models(arg)]


def test_every_field_of_every_display_model_describes_itself() -> None:
    models = _models(DisplayConfig)
    # The nested models the editor gives a section each, so a walk that stopped
    # at the top level would pass while describing nothing.
    assert len(models) > 1, "the walk found no nested models below DisplayConfig"
    undescribed = [
        f"{model.__name__}.{name}"
        for model in models
        for name, field in model.model_fields.items()
        if not (field.description or "").strip()
    ]
    assert not undescribed, (
        f"{undescribed} carry no Field(description=...), so the editor shows an "
        "unlabelled control and the generated reference page an empty cell."
    )


def test_the_schema_endpoint_carries_a_description_for_every_control(schema) -> None:
    """The descriptions have to survive the trip through the JSON schema.

    A field's own description lands on the property, except where pydantic
    moves it onto the definition the property refers to — which is why a
    section takes either (`sectionHelp`, `src/maverick/server/static/app.js`).
    """
    defs = schema["$defs"]
    undescribed = []
    for name, node in schema["properties"].items():
        nested = defs.get(str(node.get("$ref", "")).rsplit("/", 1)[-1], {})
        described = node.get("description") or (
            nested.get("description") if nested.get("properties") else ""
        )
        if not described:
            undescribed.append(name)
    for def_name, definition in defs.items():
        for name, node in (definition.get("properties") or {}).items():
            if not node.get("description"):
                undescribed.append(f"{def_name}.{name}")
    assert not undescribed, f"{undescribed} reach the editor with no help text"


def test_every_enum_the_schema_exposes_has_at_least_one_value(schema) -> None:
    """An enum field becomes a `<select>`, and an empty one offers nothing."""
    enums = {name: node["enum"] for name, node in schema["$defs"].items() if "enum" in node}
    assert enums, "the schema exposes no enums at all, and DisplayConfig has four"
    empty = sorted(name for name, values in enums.items() if not values)
    assert not empty, f"{empty} are enums with no values, so their select is empty"


def test_the_schema_endpoint_names_every_registered_transport(schema) -> None:
    """`TransportConfig` is `extra="allow"`, so this map is the only field list.

    Nothing else describes a transport's options: they are not in the model, so
    a transport missing here draws a Transport section with a type and nothing
    under it.
    """
    registered = available_transports()
    assert set(schema["transports"]) == set(registered), (
        "GET /api/schema/display and the transport registry disagree about "
        "which transports exist"
    )
    for name, info in schema["transports"].items():
        assert info["description"], f"transport {name} describes itself to nobody"
        assert set(info["options"]) == set(registered[name].options_doc), (
            f"transport {name}'s option list is not its options_doc"
        )
        assert all(info["options"].values()), (
            f"transport {name} has an option with no description, and the "
            "editor has nothing else to show under it"
        )
