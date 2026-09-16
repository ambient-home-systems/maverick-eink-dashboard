"""The displays Maverick owns, in a file it can write.

A display used to exist only in the config file, which is read once at start-up
and never written back (``load_config`` in :mod:`maverick.config`). Under the
Home Assistant app that file is ``/config/maverick.yaml``, edited with a
separate file-editor app and applied by restarting the service, so the setup UI
had nowhere to put a display somebody created in it. This module is that place:
``<data_dir>/displays.yaml`` by default — beside the frames and the state
``Engine`` already writes — or wherever ``displays_file`` points.

The file is a mapping, ``{"version": 1, "displays": [...]}``, and each entry is
a ``DisplayConfig`` with everything left at its default omitted, so a stored
display reads like a hand-written one.

One source of truth at a time. :func:`resolve_displays` decides which, on every
load of a config file:

* **the store exists** — it is the source, and a ``displays:`` list in the main
  config is ignored, with one warning naming both files;
* **no store, displays in the main config** — they are imported into the store
  once, and read from it afterwards;
* **neither** — there are no displays.

The store is machine-owned. ``${VAR}`` in a hand-edited store file is expanded
on load exactly as it is in the main config (``expand_env``), but a save writes
the *resolved* value back, so a substitution written by hand survives only
until the next save.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import Config, ConfigError, DisplayConfig, expand_env, page_name_for

log = logging.getLogger(__name__)

#: Schema version of the file this module writes. A store written by a newer
#: Maverick is refused rather than guessed at.
VERSION = 1

_HEADER = """\
# Maverick's displays. Maverick writes this file and reads it on every start; a
# save rewrites it whole, so an edit made here survives only until the next one.
# ${VAR} is expanded when this file is read, but a save writes back the value it
# expanded to. While this file exists, the `displays:` list in the main config
# file is ignored.
"""


class DisplayStore:
    """The displays Maverick manages, in a file it owns."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    # ---------------------------------------------------------------- read --

    def load(self) -> list[DisplayConfig]:
        """Read the store. Raises ``ConfigError`` on a file that is not one."""
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ConfigError(f"{self.path} must contain a YAML mapping at the top level")
        version = raw.get("version", VERSION)
        if version != VERSION:
            raise ConfigError(
                f"{self.path} is a display store of version {version!r}, and this "
                f"Maverick reads version {VERSION}. Upgrade Maverick, or move the "
                "file aside and let it be written again."
            )
        entries = raw.get("displays") or []
        if not isinstance(entries, list):
            raise ConfigError(f"{self.path}: 'displays' must be a list, one entry per panel")
        # Validating through `Config` rather than `DisplayConfig` is what makes a
        # broken store fail the way a broken config file does: the same field
        # validators, and the same duplicate-id check (`Config._unique_ids`).
        return Config.model_validate({"displays": expand_env(entries)}).displays

    # --------------------------------------------------------------- write --

    def save(self, displays: list[DisplayConfig]) -> None:
        """Replace the store with ``displays``.

        Write-then-rename, as ``FrameStore._persist`` does: a save happens while
        the service is running — and, once the setup UI can make one, while a
        panel could be reading — so nobody must ever meet half a file.
        ``OSError`` is left to the caller, which knows whether a store it cannot
        write is fatal.
        """
        payload = {
            "version": VERSION,
            "displays": [dump_display(display) for display in displays],
        }
        body = yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_text(_HEADER + body, encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
            raise


# --------------------------------------------------------------------------- #
# Dumping
# --------------------------------------------------------------------------- #

def dump_display(display: DisplayConfig) -> dict[str, Any]:
    """The shortest mapping that loads back as ``display``.

    ``model_dump(exclude_defaults=True)`` does most of it, dropping every key
    left at its default so a stored entry is as short as a hand-written one. It
    compares against the default *as written*, though, and several durations are
    written as ``"10s"`` and stored as ``10.0`` (``debounce`` in
    ``ScheduleConfig``, ``settle`` and ``timeout`` in ``RenderConfig``, all of
    which normalise in a validator), so those survive a comparison they should
    lose. Pruning against what a default display of the same id dumps removes
    them too, and keeps every ``TransportConfig`` extra, which has no default to
    be equal to — that is where each transport's own options live
    (`src/maverick/config.py`, ``TransportConfig``).
    """
    reference = DisplayConfig(id=display.id).model_dump(mode="json")
    written = _prune(display.model_dump(mode="json", exclude_defaults=True), reference)
    if written.get("pages"):
        written["pages"] = [_shorten_page(page) for page in written["pages"]]
    # `id` prunes away against a reference built from it; it is also the one key
    # an entry cannot be read without, so it leads.
    return {"id": display.id, **written}


def _shorten_page(page: dict[str, Any]) -> dict[str, Any]:
    """A page without the ``name`` it would derive for itself anyway.

    ``exclude_defaults`` cannot drop it: the validator fills the field in, so a
    derived name is no longer equal to the field's default (``PageConfig`` in
    :mod:`maverick.config`). Written out, it would pin a name that was meant to
    follow the dashboard path — and the setup UI would show it in the Name box
    rather than as the placeholder saying where it comes from.
    """
    if page.get("name") and page["name"] == page_name_for(page.get("dashboard", "")):
        return {key: value for key, value in page.items() if key != "name"}
    return page


def _prune(data: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    """``data`` without the keys ``reference`` already has the same value for."""
    kept: dict[str, Any] = {}
    for key, value in data.items():
        if key not in reference:  # a transport option: no default to compare to
            kept[key] = value
            continue
        against = reference[key]
        if isinstance(value, dict) and isinstance(against, dict):
            nested = _prune(value, against)
            if nested:
                kept[key] = nested
        elif value != against:
            kept[key] = value
    return kept


# --------------------------------------------------------------------------- #
# Precedence
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class DisplayResolution:
    """What the displays are and which file they came from.

    ``source`` is the provenance in words, which is what `maverick check`
    prints and the only reason this is not a plain list.
    """

    store: DisplayStore
    displays: list[DisplayConfig]
    source: str


def resolve_displays(config: Config, config_path: str | Path) -> DisplayResolution:
    """Choose between the store and the ``displays:`` list, importing once.

    Called by ``load_config`` and nowhere else, so every entry point that reads
    a config file — ``serve``, ``render``, ``check``, ``esphome`` — gets the same
    answer, and a ``Config`` built in memory gets no store at all.

    Nothing here mutates ``config``; the caller applies the result.
    """
    store = DisplayStore(config.display_store_path)
    if store.exists():
        if config.displays:
            log.warning(
                "displays are listed in both %s and %s; the store wins. The "
                "displays: list in %s is ignored and can be deleted.",
                config_path,
                store.path,
                config_path,
            )
        return DisplayResolution(store, store.load(), f"{store.path} (the display store)")

    if not config.displays:
        return DisplayResolution(store, [], str(config_path))

    try:
        store.save(config.displays)
    except OSError as exc:
        log.warning(
            "could not write the display store %s: %s. The displays in %s are "
            "still rendered, but they cannot be changed until the store can be "
            "written.",
            store.path,
            exc,
            config_path,
        )
        return DisplayResolution(store, config.displays, str(config_path))

    log.info(
        "imported %d display(s) from %s into %s; they are managed in the setup UI "
        "from now on, and the displays: list in %s can be deleted",
        len(config.displays),
        config_path,
        store.path,
        config_path,
    )
    return DisplayResolution(
        store, config.displays, f"{config_path} (imported into {store.path})"
    )


__all__ = ["DisplayStore", "DisplayResolution", "dump_display", "resolve_displays", "VERSION"]
