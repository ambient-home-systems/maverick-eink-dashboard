"""The generated reference must match the code it is generated from.

`docs/reference/configuration.md` is written by `scripts/gen_docs.py` from the
models in `src/maverick/config.py`. Nothing stops someone changing a field and
not regenerating except this file, so it runs the generator in check mode and
fails when the committed page is stale — which is the whole point of generating
it. `python scripts/gen_docs.py` fixes every failure here.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "scripts" / "gen_docs.py"
REFERENCE = ROOT / "docs" / "reference" / "configuration.md"
OPENAPI = ROOT / "docs" / "reference" / "openapi.json"
HTTP_API = ROOT / "docs" / "reference" / "http-api.md"
EXAMPLE = ROOT / "config.example.yaml"


def _load_generator() -> Any:
    spec = importlib.util.spec_from_file_location("gen_docs", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before execution because the module defines dataclasses, which
    # resolve their annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gen_docs = _load_generator()


def test_generated_reference_is_up_to_date() -> None:
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "the generated reference is out of date; run `python scripts/gen_docs.py` "
        f"and commit the result.\n{result.stdout}{result.stderr}"
    )


def test_generation_is_idempotent() -> None:
    """Two runs must produce the same bytes, or --check could never pass."""
    first = gen_docs.render_configuration()
    second = gen_docs.render_configuration()
    assert first == second


def test_page_opens_with_the_generated_banner() -> None:
    assert REFERENCE.read_text(encoding="utf-8").splitlines()[0] == f"<!-- {gen_docs.BANNER} -->"


def test_every_config_field_has_a_description() -> None:
    """A field with no description would be documented as a blank cell."""
    missing = [
        f"{model.__name__}.{name}"
        for model in gen_docs.reachable_models(gen_docs.Config)
        for name, info in model.model_fields.items()
        if not info.description
    ]
    assert not missing, f"add Field(description=...) in config.py for: {sorted(missing)}"


def test_every_transport_option_is_documented() -> None:
    """A transport that reads an option it does not describe fails generation."""
    gen_docs.check_transport_options()


def _documented_keys(markdown: str) -> set[str]:
    """Every key named in the first column of a key or option table."""
    keys: set[str] = set()
    collecting = False
    for line in markdown.splitlines():
        if not line.startswith("|"):
            collecting = False
            continue
        first = line.split("|")[1].strip()
        if first in ("Key", "Option"):
            collecting = True
            continue
        if first in ("Value", "Resolved value") or set(first) <= {"-", " "}:
            continue
        if collecting and first:
            keys.add(first.strip("`*"))
    return keys


def _example_keys(node: Any) -> set[str]:
    """Every mapping key used anywhere in the example config."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(str(key))
            found |= _example_keys(value)
    elif isinstance(node, list):
        for item in node:
            found |= _example_keys(item)
    return found


def test_every_example_config_key_appears_in_the_reference() -> None:
    """The worked example must not use a key the reference never mentions."""
    example = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    documented = _documented_keys(REFERENCE.read_text(encoding="utf-8"))
    undocumented = sorted(_example_keys(example) - documented)
    assert not undocumented, (
        "config.example.yaml uses keys the configuration reference does not "
        f"document: {undocumented}"
    )


@pytest.mark.parametrize("required", ["id"])
def test_required_keys_are_marked(required: str) -> None:
    text = REFERENCE.read_text(encoding="utf-8")
    assert f"| `{required}` | `str` | **required** |" in text


def test_every_route_is_documented() -> None:
    """A new route must reach docs/reference/http-api.md, not just openapi.json.

    The OpenAPI document is generated, so it follows `create_app` on its own.
    The prose page is hand-written, and this is what stops a route being added
    without anyone describing what it returns.
    """
    document = json.loads(OPENAPI.read_text(encoding="utf-8"))
    page = HTTP_API.read_text(encoding="utf-8")
    missing = sorted(path for path in document["paths"] if f"`{path}`" not in page)
    assert not missing, (
        "docs/reference/http-api.md does not mention these routes: "
        f"{missing}. Document them, then run `python scripts/gen_docs.py`."
    )


def test_ci_runs_the_esphome_validation() -> None:
    """`scripts/check_esphome.py` is only worth anything if CI runs it.

    The recipe's claim that ESPHome accepts every generated configuration
    (`docs/recipes/esphome-waveshare.md`, *Flash it*) rests on the `esphome`
    job in `.github/workflows/ci.yml`; a job removed or renamed would leave
    the claim standing on nothing.
    """
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    job = workflow["jobs"].get("esphome")
    assert job, "ci.yml has no `esphome` job"
    commands = " ".join(step.get("run", "") for step in job["steps"])
    assert "install esphome" in commands, "ESPHome itself must be installed"
    assert "python scripts/check_esphome.py" in commands
    assert (ROOT / "scripts" / "check_esphome.py").is_file()

