"""Handing a generated configuration to the ESPHome Device Builder.

Maverick does not compile or flash firmware, and should not: the PlatformIO
toolchain is a gigabyte of disk and minutes of CPU on the Pi 3 the app targets,
and the ESPHome Device Builder add-on already does both — including flashing
over Web Serial from the browser. What was missing was the hand-off. A user
had a raw YAML document in one tab and an empty *New device* form in another,
and the four things in between (the file name, three secrets, where to paste)
were all theirs to work out.

This module closes that gap two ways:

* **Where the Device Builder reads from.** The official add-on keeps its
  configurations in *Home Assistant's* config folder, under ``esphome/``: its
  manifest maps ``config:rw`` (``esphome/config.yaml`` in
  ``esphome/home-assistant-addon``), the legacy mapping that mounts Home
  Assistant's config directory at ``/config``, and its start script runs
  ``esphome-device-builder /config/esphome``
  (``docker/ha-addon-rootfs/etc/s6-overlay/s6-rc.d/esphome/run`` in
  ``esphome/esphome``). Another app sees that same directory at
  ``/homeassistant`` when it asks for the ``homeassistant_config:rw`` mapping
  (``app/config.yaml``), so ``/homeassistant/esphome`` is where a file has to
  go to appear in the Device Builder as a device to install, with no paste.
  It is *not* the add-on's own ``/addon_configs/5c53de3b_esphome`` folder,
  which the Device Builder never reads; an earlier version of this module
  wrote there, and the file appeared nowhere. Standalone,
  ``server.esphome_dir`` names the directory instead
  (``src/maverick/config.py``). ``/share/esphome`` is the last resort in the
  app: visible over Samba, but the Device Builder does not read it either,
  and the setup UI says so.
* **Which secrets are still missing.** ESPHome resolves every ``!secret`` from
  the ``secrets.yaml`` beside the configuration — the Device Builder's own
  *Secrets* editor edits that file — and fails the compile naming the first
  one it cannot find. That file is read here — read, never written: it holds
  the user's Wi-Fi password — so the setup UI can say which names still need
  adding rather than leaving the compile to say so.

The slugs are the official ESPHome add-on's, from its repository
(``esphome/home-assistant-addon``, ``esphome/config.yaml`` and the beta and dev
variants beside it): the repository's own slug prefix ``5c53de3b`` and the
add-on's ``esphome``, joined the way the Supervisor names every add-on. They
are used to find the Device Builder's page, not its files.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..ha import supervisor

log = logging.getLogger(__name__)

#: The official ESPHome add-on and its two prereleases, stable first.
ESPHOME_ADDON_SLUGS: tuple[str, ...] = (
    "5c53de3b_esphome",
    "5c53de3b_esphome-beta",
    "5c53de3b_esphome-dev",
)

#: Where the Supervisor mounts Home Assistant's config folder for an app with
#: the `homeassistant_config` mapping (`app/config.yaml`).
HOMEASSISTANT_CONFIG = Path("/homeassistant")

#: The Device Builder's own directory: `esphome/` in Home Assistant's config
#: folder, which its start script passes to `esphome-device-builder` (see the
#: module docstring).
ESPHOME_DIR_NAME = "esphome"

#: The last resort in the app: on the Samba share, where a user can copy the
#: file out of. The Device Builder does not read it.
SHARE_DIR = Path("/share/esphome")

#: The frontend route that opens an add-on's ingress page inside Home
#: Assistant, relative to Home Assistant's own origin.
INGRESS_ROUTE = "/hassio/ingress/{slug}"


class InstallError(RuntimeError):
    """The configuration could not be written where it was asked to go."""


class ExistsError(InstallError):
    """The target file exists with different content; the caller must say so."""


@dataclass(frozen=True)
class Destination:
    """A directory the ESPHome Device Builder reads configurations from."""

    id: str
    path: Path
    #: `addon` (the Device Builder's own folder), `configured` or `share`:
    #: which rule found it.
    kind: str
    label: str

    @property
    def writable(self) -> bool:
        target = self.path if self.path.exists() else self.path.parent
        import os

        return target.exists() and os.access(target, os.W_OK)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": str(self.path),
            "kind": self.kind,
            "label": self.label,
            "writable": self.writable,
            "exists": self.path.exists(),
        }


def destinations(configured: str = "") -> list[Destination]:
    """Every directory a generated file could usefully be written to, best first.

    `configured` is `server.esphome_dir`. In the app the Device Builder's own
    directory, `esphome/` in Home Assistant's config folder, comes first when
    the `homeassistant_config` mapping exposes that folder — offered even
    before the add-on has created it, since its start script does
    `mkdir -p /config/esphome` and reads whatever is there — then
    `/share/esphome`, which the Device Builder never reads. Standalone only
    the configured directory is offered, since nothing else means anything to
    whatever ESPHome the user runs.
    """
    found: list[Destination] = []
    if configured:
        found.append(
            Destination(
                "configured", Path(configured), "configured", "the ESPHome config directory"
            )
        )
    if supervisor.running_under_supervisor():
        if HOMEASSISTANT_CONFIG.is_dir():
            found.append(
                Destination(
                    "esphome",
                    HOMEASSISTANT_CONFIG / ESPHOME_DIR_NAME,
                    "addon",
                    "the ESPHome Device Builder",
                )
            )
        found.append(Destination("share", SHARE_DIR, "share", "the share folder"))
    return found


def destination(destination_id: str, configured: str = "") -> Destination:
    for candidate in destinations(configured):
        if candidate.id == destination_id:
            return candidate
    raise InstallError(f"no ESPHome destination {destination_id!r} on this host")


async def dashboard_url(frontend_url: str) -> dict[str, Any] | None:
    """Where the ESPHome Device Builder's own page is, if the add-on is installed.

    The Supervisor answers `GET /addons/<slug>/info` for any installed add-on
    at the default role (`^/.+/info$`, `supervisor/api/middleware/security.py`),
    which `hassio_api: true` in `app/config.yaml` grants. The frontend route
    `/hassio/ingress/<slug>` opens that add-on's ingress page inside Home
    Assistant, which is where its *Install* button lives. `path` is that
    route on its own; `url` joins it to `frontend_url`, which in the app is
    the *internal* address the renderer uses (`http://homeassistant:8123`,
    `app/config.yaml`) and not one a browser can open, so the setup UI
    rebuilds the link from `path` on Home Assistant's own origin when it is
    running inside Home Assistant's ingress (`haFrontendUrl`,
    `src/maverick/server/static/app.js`). Outside the Supervisor there is no
    add-on to find, and None is the answer.
    """
    if not supervisor.running_under_supervisor():
        return None
    for slug in ESPHOME_ADDON_SLUGS:
        try:
            info = await supervisor.api_get(f"/addons/{slug}/info")
        except supervisor.SupervisorError:
            continue
        if not isinstance(info, dict):
            continue
        path = INGRESS_ROUTE.format(slug=slug)
        return {
            "slug": slug,
            "name": info.get("name") or "ESPHome Device Builder",
            "version": info.get("version"),
            "state": info.get("state"),
            "path": path,
            "url": f"{frontend_url.rstrip('/')}{path}",
        }
    return None


def missing_secrets(directory: Path, names: list[str]) -> list[str] | None:
    """Which of `names` the `secrets.yaml` in `directory` does not define.

    None when there is no readable `secrets.yaml` there at all — the file has
    yet to be created, which the UI says differently from "two keys short".
    Read only: the file holds the user's Wi-Fi password, and nothing here
    should ever write to it.
    """
    path = directory / "secrets.yaml"
    if not path.is_file():
        return None
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("could not read %s: %s", path, exc)
        return None
    present = set(loaded) if isinstance(loaded, dict) else set()
    return [name for name in names if name not in present]


def install(target: Destination, filename: str, document: str, *, overwrite: bool) -> Path:
    """Write `document` as `filename` into `target`.

    An existing file with the same content is left alone and reported as
    written. One with different content is the user's — the recipe tells them
    the generated file is theirs to edit — so it is replaced only when they
    said to, and `ExistsError` otherwise.
    """
    path = target.path / filename
    try:
        target.path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InstallError(f"cannot create {target.path}: {exc}") from exc
    if path.exists():
        try:
            current = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InstallError(f"cannot read {path}: {exc}") from exc
        if current == document:
            return path
        if not overwrite:
            raise ExistsError(
                f"{path.name} already exists in {target.label} with different content. "
                "Replace it to lose any edits made there."
            )
    try:
        path.write_text(document, encoding="utf-8")
    except OSError as exc:
        raise InstallError(f"cannot write {path}: {exc}") from exc
    log.info("wrote ESPHome configuration %s", path)
    return path


__all__ = [
    "ESPHOME_ADDON_SLUGS",
    "ESPHOME_DIR_NAME",
    "HOMEASSISTANT_CONFIG",
    "INGRESS_ROUTE",
    "SHARE_DIR",
    "Destination",
    "ExistsError",
    "InstallError",
    "dashboard_url",
    "destination",
    "destinations",
    "install",
    "missing_secrets",
]
