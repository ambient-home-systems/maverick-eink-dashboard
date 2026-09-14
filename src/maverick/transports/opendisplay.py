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
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from ..eink import Frame
from ..eink.dither import DitherMode
from .base import DeliveryContext, DeliveryResult, Transport, register

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

    async def deliver(self, frame: Frame, context: DeliveryContext) -> DeliveryResult:
        mode = str(self.option("mode", "auto")).lower()
        if mode == "auto":
            mode = "ha" if context.services.get("ha") is not None else "ble"
        if mode == "ha":
            return await self._deliver_via_ha(frame, context)
        if mode == "ble":
            return await self._deliver_via_ble(frame, context)
        raise ValueError(f"opendisplay mode must be auto, ha or ble (got {mode!r})")

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
        mode = str(self.option("mode", "auto")).lower()
        if mode in ("auto", "ha") and context.services.get("ha") is not None:
            device_id = self.option("device_id")
            if not device_id:
                return DeliveryResult.failure("transport.device_id is not set")
            return DeliveryResult.success(f"will upload via Home Assistant to {device_id}")
        try:
            import opendisplay as od
        except ImportError:
            return DeliveryResult.failure("py-opendisplay is not installed")
        found = await od.discover_devices(timeout=float(self.option("scan_timeout", 10.0)))
        mac = (self.option("mac") or "").upper()
        if mac and mac in {m.upper() for m in found.values()}:
            return DeliveryResult.success(f"tag {mac} is in range")
        listing = ", ".join(f"{n} ({m})" for n, m in found.items()) or "none"
        return DeliveryResult.failure(f"tag {mac or '(unset)'} not found. In range: {listing}")


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
