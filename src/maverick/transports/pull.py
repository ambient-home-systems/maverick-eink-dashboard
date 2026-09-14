"""HTTP pull and file transports — for devices that fetch on their own schedule.

A battery panel spends almost all its life asleep. It wakes, asks for a frame,
draws it if it changed, and sleeps again. Nothing can be pushed to it, so these
transports publish the frame and let the device collect it.

Two details do most of the work for battery life:

``ETag`` / ``304``
    The device sends the checksum of what it is already showing. If nothing has
    changed it gets a 304 with no body — no download, and more importantly no
    e-ink refresh, which is what actually costs the energy.
``Date``
    Every response carries the server clock, so firmware can keep time without
    an SNTP client.

Both details are why the device-side recipes stay short: ``docs/recipes/``
covers ESPHome, a jailbroken Kindle or Kobo, and TRMNL against this transport,
and ``webhook-and-file.md`` against the other two here.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import ClassVar

from ..eink import Frame
from .base import DeliveryContext, DeliveryResult, Transport, register

log = logging.getLogger(__name__)


@register
class HttpPullTransport(Transport):
    """Publish the frame for a device to fetch from Maverick's own HTTP server.

    Delivery is *pending* until the device actually collects it; the server
    records each fetch so ``/api/displays`` can report when a panel last woke.
    """

    name = "http_pull"
    pushes = False
    description = "Device fetches frames over HTTP (ESP32, ESPHome, Kindle, TRMNL)"
    options_doc: ClassVar[dict[str, str]] = {}

    async def deliver(self, frame: Frame, context: DeliveryContext) -> DeliveryResult:
        store = context.services.get("frames")
        if store is None:
            return DeliveryResult.failure("frame store unavailable (server not running)")
        store.put(context.display.id, frame, context)
        base = (context.config.server.base_url or "").rstrip("/")
        url = f"{base}/api/displays/{context.display.id}/frame"
        return DeliveryResult.awaiting_pull(
            f"available at {url}", bytes_sent=len(frame.payload)
        )

    async def probe(self, context: DeliveryContext) -> DeliveryResult:
        if not context.config.server.base_url:
            return DeliveryResult.failure(
                "server.base_url is not set, so devices cannot be told where to "
                "fetch from. Set it to a URL reachable from the panel."
            )
        return DeliveryResult.success(f"serving from {context.config.server.base_url}")


@register
class FileTransport(Transport):
    """Write the frame to disk.

    For anything that reads a file: a jailbroken Kindle rsyncing a screensaver,
    a Samba share, a separate web server, or just eyeballing output while you
    tune a theme.
    """

    name = "file"
    pushes = True
    description = "Write frames to a directory (Kindle screensaver, Samba, debugging)"
    options_doc: ClassVar[dict[str, str]] = {
        "path": "Directory to write frames into, created if it does not exist. Default `./out`.",
        "filename": (
            "Name of the frame file. Unset uses the display id with an extension from the "
            "frame format (`.png`, `.bmp`, or `.bin` for a raw layout)."
        ),
        "write_preview": (
            "Also write `<display id>-preview.png`, a viewable render of the quantized "
            "frame. Default false."
        ),
    }

    async def deliver(self, frame: Frame, context: DeliveryContext) -> DeliveryResult:
        directory = Path(self.option("path", "./out"))
        filename = self.option("filename") or f"{context.display.id}.{_extension(frame)}"
        started = time.perf_counter()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / filename
            # Write-then-rename: a device polling the directory never sees a
            # half-written file and draws a torn frame.
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_bytes(frame.payload)
            temporary.replace(target)
        except OSError as exc:
            return DeliveryResult.failure(f"could not write {directory}: {exc}")

        if self.option("write_preview", False):
            frame.preview.save(directory / f"{context.display.id}-preview.png")

        return DeliveryResult.success(
            f"wrote {target}",
            bytes_sent=len(frame.payload),
            duration_s=time.perf_counter() - started,
        )


@register
class WebhookTransport(Transport):
    """POST the frame to an arbitrary URL.

    The escape hatch: any device or service with an HTTP endpoint, including
    other dashboard servers and vendor cloud APIs.
    """

    name = "webhook"
    pushes = True
    description = "POST frames to a custom HTTP endpoint"
    options_doc: ClassVar[dict[str, str]] = {
        "url": "Endpoint the frame is sent to.",
        "method": "HTTP method. Default `POST`.",
        "headers": (
            "Extra request headers. `Content-Type`, `X-Maverick-Display` and "
            "`X-Maverick-Checksum` are set for you unless you give them here."
        ),
        "timeout": "Request timeout in seconds. Default 30.",
    }

    async def deliver(self, frame: Frame, context: DeliveryContext) -> DeliveryResult:
        import httpx

        url = self.option("url", required=True)
        method = str(self.option("method", "POST")).upper()
        headers = dict(self.option("headers", {}) or {})
        headers.setdefault("Content-Type", _content_type(frame))
        headers.setdefault("X-Maverick-Display", context.display.id)
        headers.setdefault("X-Maverick-Checksum", frame.checksum)

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=float(self.option("timeout", 30))) as client:
                response = await client.request(
                    method, url, content=frame.payload, headers=headers
                )
            if response.status_code >= 400:
                return DeliveryResult.failure(
                    f"{method} {url} returned {response.status_code}: {response.text[:200]}"
                )
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult.failure(f"{method} {url} failed: {exc}")

        return DeliveryResult.success(
            f"{method} {url} -> {response.status_code}",
            bytes_sent=len(frame.payload),
            duration_s=time.perf_counter() - started,
        )


def _extension(frame: Frame) -> str:
    return {"png": "png", "bmp": "bmp"}.get(frame.frame_format.value, "bin")


def _content_type(frame: Frame) -> str:
    return {"png": "image/png", "bmp": "image/bmp"}.get(
        frame.frame_format.value, "application/octet-stream"
    )


__all__ = ["HttpPullTransport", "FileTransport", "WebhookTransport"]
