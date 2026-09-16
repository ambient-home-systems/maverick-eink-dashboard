"""OpenDisplay transport — BLE e-paper tags.

OpenDisplay is a BLE-only open standard: a sender pushes a rendered image to an
e-paper receiver over GATT, chunked and acknowledged, with optional compression
and AES-128 encryption. There is deliberately no access point to set up.

Maverick can reach a tag two ways, and the difference matters a lot in practice:

``mode: ha`` (default)
    Hand the frame to Home Assistant's own ``opendisplay.upload_image`` action.
    HA then delivers it over whatever Bluetooth it has — including **ESPHome
    Bluetooth proxies**, which is the whole point: a proxy in the room with the
    tag gives you coverage a server in a cupboard cannot match. This works from
    a container with no Bluetooth hardware at all, which is the normal case for
    a Home Assistant add-on.

``mode: ble``
    Talk to the tag directly with ``py-opendisplay``. Needs a real adapter
    visible to the process, and only reaches tags in range of *that* adapter.
    Useful when running Maverick standalone, or for setup and diagnostics.

One subtlety worth stating plainly: Maverick has already quantised the frame to
the panel's palette, applying content-aware dithering that protects text. If the
library dithered again it would undo that work, so the already-exact preview is
sent with dithering disabled.

**In the Home Assistant app, ``ble`` is refused outright.** The app's manifest
asks for no Bluetooth access (``app/config.yaml`` declares no ``host_dbus`` and
no device), so a local adapter is never visible to it, and a display set to
``ble`` there would fail on every schedule with a BLE error that reads like a
range problem. ``_resolve_mode`` says so once, in words, and the setup UI does
not offer the mode in the app at all (`GET /api/environment`,
`src/maverick/server/api.py`).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, ClassVar

from ..eink import Frame
from ..eink.dither import DitherMode
from ..ha import supervisor
from .base import DeliveryContext, DeliveryResult, OptionField, Transport, register

log = logging.getLogger(__name__)

#: Maverick dither mode -> py-opendisplay DitherMode attribute name.
_DITHER_NAMES = {
    DitherMode.NONE: "NONE",
    DitherMode.ORDERED: "ORDERED",
    DitherMode.FLOYD_STEINBERG: "FLOYD_STEINBERG",
    DitherMode.ATKINSON: "ATKINSON",
    DitherMode.BURKES: "BURKES",
    DitherMode.SIERRA: "SIERRA",
    DitherMode.SIERRA_LITE: "SIERRA_LITE",
    DitherMode.STUCKI: "STUCKI",
    DitherMode.JARVIS: "JARVIS_JUDICE_NINKE",
    # AUTO has no library equivalent: we already did it, so do not redo it.
    DitherMode.AUTO: "NONE",
}


@register
class OpenDisplayTransport(Transport):
    """Deliver frames to OpenDisplay BLE e-paper tags."""

    name = "opendisplay"
    pushes = True
    description = "OpenDisplay BLE tags, via Home Assistant BLE proxies or a local adapter"
    options_doc: ClassVar[dict[str, str]] = {
        "mode": (
            "`ha` uploads through Home Assistant's own Bluetooth, including ESPHome "
            "Bluetooth proxies; `ble` talks to the tag from this host and needs a local "
            "adapter. Default `auto`, which is `ha` when Home Assistant is connected and "
            "`ble` otherwise."
        ),
        "device_id": (
            "Device registry id from the OpenDisplay integration — not the entity id and "
            "not the MAC. Required in `ha` mode."
        ),
        "media_dir": (
            "Directory both Maverick and Home Assistant can read, where the frame is "
            "written as a PNG for the upload action to pick up. Default `/media/maverick`; "
            "the add-on needs the `media:rw` mapping for it."
        ),
        "media_root": (
            "Root of Home Assistant's media folder. `media_dir` must sit inside it, because "
            "Home Assistant can only read images from there. Default `/media`."
        ),
        "media_source_prefix": (
            "Media source prefix prepended to the frame's path in the upload action. "
            "Default `media-source://media_source/local`."
        ),
        "rotation": (
            "Rotation passed to `opendisplay.upload_image` in `ha` mode. Unset leaves it to "
            "the integration. This is the tag's own rotation, not `displays[].rotation`, "
            "which Maverick has already applied to the image."
        ),
        "mac": (
            "Tag MAC address, for `ble` mode. Either this or `device_name` is required "
            "there; `maverick scan` lists both for the tags in range."
        ),
        "device_name": "Advertised tag name, for `ble` mode when `mac` is not known.",
        "encryption_key": (
            "AES-128 key as hex, for tags that require encrypted transfers. Unset sends "
            "unencrypted."
        ),
        "timeout": "Seconds to wait for a BLE connection, in `ble` mode. Default 20.",
        "max_attempts": "BLE connection attempts before giving up, in `ble` mode. Default 4.",
        "scan_timeout": (
            "Seconds to scan for tags when `maverick check` or the setup UI's *Test "
            "delivery* probes a `ble` transport. Default 10."
        ),
    }
    #: How the setup UI asks for each of those. `mode` gates the rest: an `ha`
    #: user sees the device picker and nothing else until they open Advanced;
    #: a `ble` user sees the MAC and a scan button. `auto` shows both sets,
    #: since which one applies is decided at delivery time.
    mode_option: ClassVar[str] = "mode"
    option_fields: ClassVar[dict[str, OptionField]] = {
        "mode": OptionField(
            kind="select", choices=("auto", "ha", "ble"), default="auto",
            label="How the tag is reached",
        ),
        "device_id": OptionField(
            modes=("auto", "ha"), required=True, kind="ha_device",
            integration="opendisplay", label="Tag in Home Assistant",
        ),
        "rotation": OptionField(
            modes=("auto", "ha"), advanced=True, kind="select",
            choices=("0", "90", "180", "270"), label="Tag rotation",
        ),
        "media_dir": OptionField(
            modes=("auto", "ha"), advanced=True, kind="path", default="/media/maverick",
            label="Media directory",
        ),
        "media_root": OptionField(
            modes=("auto", "ha"), advanced=True, kind="path", default="/media",
            label="Media root",
        ),
        "media_source_prefix": OptionField(
            modes=("auto", "ha"), advanced=True, default="media-source://media_source/local",
            label="Media source prefix",
        ),
        "mac": OptionField(modes=("auto", "ble"), required=True, kind="mac", label="Tag MAC"),
        "device_name": OptionField(
            modes=("auto", "ble"), advanced=True, label="Advertised name (instead of MAC)"
        ),
        "encryption_key": OptionField(
            modes=("auto", "ble"), advanced=True, kind="secret", label="AES-128 key (hex)"
        ),
        "timeout": OptionField(
            modes=("auto", "ble"), advanced=True, kind="number", default=20,
            label="Connect timeout (s)",
        ),
        "max_attempts": OptionField(
            modes=("auto", "ble"), advanced=True, kind="number", default=4,
            label="Connection attempts",
        ),
        "scan_timeout": OptionField(
            modes=("auto", "ble"), advanced=True, kind="number", default=10,
            label="Scan timeout (s)",
        ),
    }

    def _resolve_mode(self, context: DeliveryContext) -> tuple[str, DeliveryResult | None]:
        """The mode this delivery runs in, or the failure that says why none can.

        `auto` follows the Home Assistant connection. Under the Supervisor a
        local adapter is never available (module docstring), so `ble` there is
        refused with the reason rather than left to fail on the first connect.
        """
        mode = str(self.option("mode", "auto")).lower()
        in_app = supervisor.running_under_supervisor()
        has_ha = context.services.get("ha") is not None
        if mode == "auto":
            mode = "ha" if has_ha or in_app else "ble"
        if mode == "ble" and in_app:
            return mode, DeliveryResult.failure(
                "opendisplay mode 'ble' is not available in the Home Assistant app: it "
                "has no Bluetooth of its own. Use mode: ha, which delivers through Home "
                "Assistant's Bluetooth and its ESPHome proxies."
            )
        if mode not in ("ha", "ble"):
            raise ValueError(f"opendisplay mode must be auto, ha or ble (got {mode!r})")
        return mode, None

    async def deliver(self, frame: Frame, context: DeliveryContext) -> DeliveryResult:
        mode, refused = self._resolve_mode(context)
        if refused is not None:
            return refused
        if mode == "ha":
            return await self._deliver_via_ha(frame, context)
        return await self._deliver_via_ble(frame, context)

    # ------------------------------------------------------------ via HA --

    async def _deliver_via_ha(self, frame: Frame, context: DeliveryContext) -> DeliveryResult:
        client = context.services.get("ha")
        if client is None:
            return DeliveryResult.failure(
                "opendisplay mode 'ha' needs a Home Assistant connection; set "
                "home_assistant.url and home_assistant.token, or use mode: ble."
            )
        device_id = self.option("device_id", required=True)

        # The HA action reads the image from a media source, so the frame has to
        # land somewhere HA can see. In the add-on that is the shared /media
        # mount; standalone it is whatever the user maps in.
        media_dir = Path(self.option("media_dir", "/media/maverick"))
        media_root = Path(self.option("media_root", "/media"))
        try:
            media_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return DeliveryResult.failure(
                f"cannot write to {media_dir} ({exc}). The add-on needs the "
                "'media:rw' mapping, or set transport.media_dir to a shared path."
            )

        path = media_dir / f"{context.display.id}.png"
        payload = frame.payload if frame.frame_format.value == "png" else _as_png(frame)
        path.write_bytes(payload)

        try:
            relative = path.relative_to(media_root).as_posix()
        except ValueError:
            return DeliveryResult.failure(
                f"media_dir {media_dir} is not inside media_root {media_root}; "
                "Home Assistant can only read images from its media folder."
            )
        prefix = self.option("media_source_prefix", "media-source://media_source/local")
        content_id = f"{prefix}/{relative}"

        data: dict[str, Any] = {
            "device_id": device_id,
            "image": {"media_content_id": content_id, "media_content_type": "image/png"},
        }
        if (rotation := self.option("rotation")) is not None:
            data["rotation"] = rotation

        started = time.perf_counter()
        try:
            await client.call_service("opendisplay", "upload_image", data)
        except Exception as exc:  # noqa: BLE001 - message is for the user
            return DeliveryResult.failure(
                f"opendisplay.upload_image failed: {exc}. Check the OpenDisplay "
                "integration is set up and device_id is correct — it is the "
                "device registry id, not the entity id or the MAC."
            )
        return DeliveryResult.success(
            f"uploaded via Home Assistant to device {device_id}",
            bytes_sent=len(payload),
            duration_s=time.perf_counter() - started,
        )

    # ----------------------------------------------------------- via BLE --

    async def _deliver_via_ble(self, frame: Frame, context: DeliveryContext) -> DeliveryResult:
        try:
            import opendisplay as od
        except ImportError:
            return DeliveryResult.failure(
                "py-opendisplay is not installed. Install it with "
                "`pip install 'maverick-eink-dashboard[opendisplay]'`, or use "
                "mode: ha to deliver through Home Assistant's Bluetooth instead."
            )

        mac = self.option("mac")
        device_name = self.option("device_name")
        if not mac and not device_name:
            raise ValueError(
                "opendisplay mode 'ble' needs either 'mac' or 'device_name'. "
                "Run `maverick scan` to find your tags."
            )

        key = self.option("encryption_key")
        encryption_key = bytes.fromhex(key) if key else None

        refresh = od.RefreshMode.FULL if context.full_refresh else od.RefreshMode.FAST
        if not context.display.profile.supports_partial:
            refresh = od.RefreshMode.FULL

        dither_name = _DITHER_NAMES.get(context.display.config.image.dither, "NONE")
        dither = getattr(od.DitherMode, dither_name, od.DitherMode.NONE)

        started = time.perf_counter()
        try:
            async with od.OpenDisplayDevice(
                mac_address=mac,
                device_name=device_name,
                encryption_key=encryption_key,
                timeout=float(self.option("timeout", 20.0)),
                max_attempts=int(self.option("max_attempts", 4)),
            ) as device:
                await device.upload_image(
                    frame.preview,
                    refresh_mode=refresh,
                    # Already quantised by Maverick; re-dithering would undo the
                    # text-preserving work the pipeline just did.
                    dither_mode=dither,
                    fit=od.FitMode.STRETCH,
                    rotate=od.Rotation.ROTATE_0,
                )
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            return DeliveryResult.failure(
                f"BLE upload to {mac or device_name} failed: {exc}"
            )

        return DeliveryResult.success(
            f"uploaded over BLE to {mac or device_name} ({refresh.name.lower()} refresh)",
            bytes_sent=frame.width * frame.height * frame.palette.bits_per_pixel // 8,
            duration_s=time.perf_counter() - started,
        )

    # ------------------------------------------------------------- probe --

    async def probe(self, context: DeliveryContext) -> DeliveryResult:
        """Say whether a delivery would reach the tag, without sending one.

        In `ha` mode that is: a device id is set, and Home Assistant's device
        registry knows it as an OpenDisplay device — the check that catches
        the entity id or the MAC pasted where the registry id goes, which the
        upload action itself reports only as a generic failure. In `ble` mode
        it is a scan: is the tag in range of this host's adapter right now.
        `maverick check` runs this per display, and the setup UI's *Test
        delivery* runs it for a display that is not saved yet
        (`Engine.probe_candidate`, `src/maverick/engine.py`).
        """
        mode, refused = self._resolve_mode(context)
        if refused is not None:
            return refused
        if mode == "ha":
            return await self._probe_via_ha(context)
        try:
            import opendisplay as od
        except ImportError:
            return DeliveryResult.failure("py-opendisplay is not installed")
        mac = str(self.option("mac") or "").upper()
        device_name = str(self.option("device_name") or "")
        if not mac and not device_name:
            return DeliveryResult.failure(
                "neither transport.mac nor transport.device_name is set; scan for the tag "
                "from the setup UI or with `maverick scan`"
            )
        found = await od.discover_devices(timeout=float(self.option("scan_timeout", 10.0)))
        if mac and mac in {m.upper() for m in found.values()}:
            return DeliveryResult.success(f"tag {mac} is in range")
        if device_name and device_name in found:
            return DeliveryResult.success(f"tag {device_name} ({found[device_name]}) is in range")
        listing = ", ".join(f"{n} ({m})" for n, m in found.items()) or "none"
        return DeliveryResult.failure(
            f"tag {mac or device_name or '(unset)'} not found. In range: {listing}"
        )

    async def _probe_via_ha(self, context: DeliveryContext) -> DeliveryResult:
        client = context.services.get("ha")
        if client is None:
            return DeliveryResult.failure(
                "opendisplay mode 'ha' needs a Home Assistant connection; set "
                "home_assistant.url and home_assistant.token, or use mode: ble."
            )
        device_id = str(self.option("device_id") or "")
        if not device_id:
            return DeliveryResult.failure("transport.device_id is not set")
        try:
            devices = await client.list_devices("opendisplay")
        except Exception as exc:  # noqa: BLE001 - a listing failure is a caveat, not a verdict
            return DeliveryResult.success(
                f"will upload via Home Assistant to {device_id} (could not check the "
                f"device registry: {exc})"
            )
        match = next((d for d in devices if d["id"] == device_id), None)
        if match is None:
            return DeliveryResult.failure(
                f"device_id {device_id!r} is not a device of the OpenDisplay integration "
                "in Home Assistant; pick the tag from the list in the setup UI, or copy "
                "the id from the device page URL."
            )
        return DeliveryResult.success(
            f"Home Assistant knows {match['name']} ({match.get('model') or 'model unknown'}) "
            "as an OpenDisplay tag"
        )


def _as_png(frame: Frame) -> bytes:
    """Re-encode a frame as PNG when the configured format is a raw layout."""
    from io import BytesIO

    buffer = BytesIO()
    frame.preview.save(buffer, format="PNG")
    return buffer.getvalue()


async def scan(timeout: float = 10.0) -> dict[str, str]:
    """Discover OpenDisplay tags in range. Used by the CLI and the setup UI."""
    import opendisplay as od

    return await od.discover_devices(timeout=timeout)


__all__ = ["OpenDisplayTransport", "scan"]
