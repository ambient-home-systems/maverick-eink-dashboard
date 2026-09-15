"""The shipped example config must load, and must match the one in the repo root.

``maverick init`` prints the packaged copy under ``src/maverick/``; the copy at
the repository root is what README.md links to and what people read on GitHub.
Two files means they can drift, so assert here that they have not.
"""

from __future__ import annotations

from pathlib import Path

from maverick.config import load_config

ROOT = Path(__file__).resolve().parent.parent
PACKAGED = ROOT / "src" / "maverick" / "config.example.yaml"
PUBLISHED = ROOT / "config.example.yaml"


def test_packaged_example_matches_repository_root() -> None:
    assert PACKAGED.read_text(encoding="utf-8") == PUBLISHED.read_text(encoding="utf-8")


def test_example_config_loads(monkeypatch) -> None:
    """The file as published, read as a file and nothing more.

    ``use_display_store=False`` keeps the load free of side effects: the example
    leaves ``data_dir`` at ``./data``, so the display store step would import
    these two displays into the working directory of whoever ran the suite
    (``resolve_displays`` in ``src/maverick/store.py``). The store's own
    behaviour is ``tests/test_display_store.py``.
    """
    monkeypatch.setenv("HA_TOKEN", "test-token")
    config = load_config(PUBLISHED, use_display_store=False)
    assert [d.id for d in config.displays] == ["kitchen", "hallway-tag"]
    assert config.display("kitchen").transport.type == "http_pull"
    assert config.display("hallway-tag").transport.type == "opendisplay"
