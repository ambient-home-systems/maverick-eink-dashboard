"""The render engine: orchestrates render, process, lint and deliver.

Also the home of the small amount of per-display state that has to survive a
restart. Two pieces of state earn their keep:

``last_checksum``
    Lets a scheduled render that produced an identical frame skip delivery. On
    a battery panel this is the single biggest lever on runtime — most renders
    of most dashboards change nothing, and an e-ink refresh is orders of
    magnitude more expensive than the render that produced it.
``frames_since_full``
    E-ink accumulates ghosting across fast/partial refreshes. Panels need a
    periodic flashing full refresh to clear it. Counting across restarts means
    a service that restarts often does not quietly stop clearing ghosts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from .config import Config, ResolvedDisplay
from .eink import Frame, FrameFormat, PipelineOptions, process
from .eink.dither import DitherMode
from .eink.pipeline import FitMode
from .ha import HomeAssistantClient
from .render import BrowserPool, DashboardRenderer, RenderError
from .transports import (
    DeliveryContext,
    DeliveryResult,
    MqttPublisher,
    availability_topic,
    get_transport,
)
from .transports.base import Transport

log = logging.getLogger(__name__)


@dataclass
class DisplayState:
    """Mutable per-display state, persisted to ``data_dir``."""

    sequence: int = 0
    frames_since_full: int = 0
    last_checksum: str = ""
    last_render_at: str = ""
    last_delivery_at: str = ""
    last_error: str = ""
    consecutive_failures: int = 0
    last_pulled_at: str = ""
    render_count: int = 0
    skip_count: int = 0


@dataclass
class RenderOutcome:
    display_id: str
    ok: bool
    skipped: bool = False
    reason: str = ""
    frame: Frame | None = None
    delivery: DeliveryResult | None = None
    render_s: float = 0.0
    process_s: float = 0.0
    total_s: float = 0.0
    trigger: str = "manual"
    full_refresh: bool = False

    def describe(self) -> str:
        if self.skipped:
            return f"[{self.display_id}] skipped: {self.reason}"
        if not self.ok:
            return f"[{self.display_id}] FAILED: {self.reason}"
        lint = self.frame.lint.summary() if self.frame else "?"
        detail = self.delivery.detail if self.delivery else ""
        return (
            f"[{self.display_id}] ok in {self.total_s:.2f}s "
            f"(render {self.render_s:.2f}s) lint={lint} — {detail}"
        )


@dataclass
class StoredFrame:
    """A rendered frame, in the form the pull endpoint and UI actually need.

    Deliberately not the full :class:`~maverick.eink.Frame`: serving a device
    needs the payload bytes and its identity, nothing more. Keeping this small
    is what lets it round-trip through disk.
    """

    display_id: str
    payload: bytes
    preview_png: bytes
    checksum: str
    width: int
    height: int
    colors: int
    frame_format: str
    media_type: str
    rendered_at: str
    lint_summary: str = ""
    lint_issues: list[dict[str, str]] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def etag(self) -> str:
        return f'"{self.checksum}"'

    def metadata(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("payload")
        data.pop("preview_png")
        return data


class FrameStore:
    """Holds the latest frame per display, for pull transports and the UI.

    Frames are persisted. Without that, a restart leaves a pull-transport
    display with a ``last_checksum`` that suppresses re-rendering but no frame
    to serve, so a sleeping panel wakes to a 404 and keeps showing a stale
    screen until the dashboard content happens to change. Writing the frame to
    disk also means a device that wakes seconds after a restart is served
    immediately rather than waiting for the next scheduled render.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self._frames: dict[str, StoredFrame] = {}
        self._pulled: dict[str, datetime] = {}
        self._directory = directory

    # ------------------------------------------------------------- memory --

    def put(self, display_id: str, frame: Frame, context: DeliveryContext | None = None) -> None:
        del context  # kept for signature stability; transports pass it
        buffer = BytesIO()
        frame.preview.save(buffer, format="PNG")
        stored = StoredFrame(
            display_id=display_id,
            payload=frame.payload,
            preview_png=buffer.getvalue(),
            checksum=frame.checksum,
            width=frame.width,
            height=frame.height,
            colors=len(frame.palette),
            frame_format=frame.frame_format.value,
            media_type=_media_type(frame.frame_format.value),
            rendered_at=_now(),
            lint_summary=frame.lint.summary(),
            lint_issues=[
                {
                    "code": i.code,
                    "severity": i.severity.value,
                    "message": i.message,
                    "hint": i.hint,
                }
                for i in frame.lint.issues
            ],
            metrics=dict(frame.metrics),
        )
        self._frames[display_id] = stored
        self._persist(stored)

    def get(self, display_id: str) -> StoredFrame | None:
        return self._frames.get(display_id)

    def mark_pulled(self, display_id: str) -> datetime:
        stamp = datetime.now(UTC)
        self._pulled[display_id] = stamp
        return stamp

    def last_pulled(self, display_id: str) -> datetime | None:
        return self._pulled.get(display_id)

    def __contains__(self, display_id: str) -> bool:
        return display_id in self._frames

    # --------------------------------------------------------------- disk --

    def _paths(self, display_id: str) -> tuple[Path, Path, Path]:
        assert self._directory is not None
        base = self._directory
        return (
            base / f"{display_id}.frame",
            base / f"{display_id}.preview.png",
            base / f"{display_id}.json",
        )

    def _persist(self, stored: StoredFrame) -> None:
        if self._directory is None:
            return
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            frame_path, preview_path, meta_path = self._paths(stored.display_id)
            # Write-then-rename so a device fetching mid-write never sees a
            # truncated frame.
            for path, payload in (
                (frame_path, stored.payload),
                (preview_path, stored.preview_png),
                (meta_path, json.dumps(stored.metadata(), indent=2).encode()),
            ):
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_bytes(payload)
                temporary.replace(path)
        except OSError as exc:
            log.warning("could not persist frame for %s: %s", stored.display_id, exc)

    def load(self, display_ids: list[str]) -> int:
        """Restore persisted frames at startup. Returns how many were found."""
        if self._directory is None or not self._directory.exists():
            return 0
        restored = 0
        for display_id in display_ids:
            frame_path, preview_path, meta_path = self._paths(display_id)
            if not (frame_path.exists() and meta_path.exists()):
                continue
            try:
                meta = json.loads(meta_path.read_text())
                self._frames[display_id] = StoredFrame(
                    payload=frame_path.read_bytes(),
                    preview_png=preview_path.read_bytes() if preview_path.exists() else b"",
                    **meta,
                )
                restored += 1
            except Exception as exc:  # noqa: BLE001 - a bad cache must not be fatal
                log.warning("ignoring unreadable stored frame for %s: %s", display_id, exc)
        if restored:
            log.info("restored %d frame(s) from %s", restored, self._directory)
        return restored


def _media_type(fmt: str) -> str:
    return {"png": "image/png", "bmp": "image/bmp"}.get(fmt, "application/octet-stream")


class Engine:
    """Owns every long-lived resource and renders displays on demand."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.data_dir = Path(config.data_dir)
        self.frames = FrameStore(self.data_dir / "frames")
        self.states: dict[str, DisplayState] = {}

        self._pool = BrowserPool(max_concurrent=int(_env_int("MAVERICK_MAX_RENDERS", 2)))
        self._ha: HomeAssistantClient | None = None
        self._mqtt: MqttPublisher | None = None
        self._renderer: DashboardRenderer | None = None
        self._transports: dict[str, Transport] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: list[Any] = []
        #: Called after every render outcome. Used by the app layer to publish
        #: MQTT state without the engine having to know MQTT exists.
        self._listeners: list[Callable[[RenderOutcome], Awaitable[None]]] = []
        self._started = False

    def add_listener(self, callback: Callable[[RenderOutcome], Awaitable[None]]) -> None:
        self._listeners.append(callback)

    async def _notify(self, outcome: RenderOutcome) -> None:
        for listener in self._listeners:
            try:
                await listener(outcome)
            except Exception:  # noqa: BLE001 - a listener must not fail a render
                log.exception("render listener failed")

    # ------------------------------------------------------------ lifecycle --

    async def start(self) -> None:
        if self._started:
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._load_state()
        self.frames.load([d.id for d in self.config.enabled_displays])

        if self.config.home_assistant.token:
            self._ha = HomeAssistantClient(self.config.home_assistant)
            try:
                info = await self._ha.check()
                log.info("connected to Home Assistant %s", info.get("version", "?"))
            except Exception as exc:  # noqa: BLE001 - keep serving without HA
                log.error("Home Assistant check failed: %s", exc)
        else:
            log.warning(
                "no Home Assistant token configured — dashboard rendering will "
                "fail until home_assistant.token is set"
            )

        self._renderer = DashboardRenderer(self.config.home_assistant, self._pool)

        if self.config.mqtt.enabled:
            # The will has to be registered before the client connects, so it
            # is built here rather than bolted on once discovery exists.
            will = (availability_topic(self.config.mqtt.base_topic), "offline")
            self._mqtt = MqttPublisher(self.config.mqtt, will=will)
            try:
                await self._mqtt.start()
            except Exception as exc:  # noqa: BLE001
                log.error("MQTT unavailable: %s", exc)
                self._mqtt = None

        for display in self.config.enabled_displays:
            transport = get_transport(display.transport.type, _transport_options(display))
            await transport.start()
            self._transports[display.id] = transport
            self._locks[display.id] = asyncio.Lock()
            self.states.setdefault(display.id, DisplayState())

        self._started = True
        log.info("engine ready with %d display(s)", len(self._transports))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        for transport in self._transports.values():
            await transport.stop()
        if self._mqtt is not None:
            await self._mqtt.stop()
        if self._ha is not None:
            await self._ha.close()
        await self._pool.stop()
        self._save_state()
        self._started = False

    @property
    def ha(self) -> HomeAssistantClient | None:
        return self._ha

    @property
    def mqtt(self) -> MqttPublisher | None:
        return self._mqtt

    def services(self) -> dict[str, Any]:
        return {"ha": self._ha, "mqtt": self._mqtt, "frames": self.frames, "engine": self}

    # --------------------------------------------------------------- render --

    async def render(
        self,
        display_id: str,
        trigger: str = "manual",
        force: bool = False,
        deliver: bool = True,
    ) -> RenderOutcome:
        """Render one display and deliver the frame.

        Serialised per display: a manual refresh arriving while the scheduler is
        mid-render would otherwise have both writing to the same panel.
        """
        display = self.config.display(display_id).resolved()
        state = self.states.setdefault(display_id, DisplayState())
        lock = self._locks.setdefault(display_id, asyncio.Lock())

        async with lock:
            started = time.perf_counter()
            outcome = RenderOutcome(display_id=display_id, ok=False, trigger=trigger)

            if self._renderer is None:
                outcome.reason = "engine not started"
                return outcome

            try:
                result = await self._renderer.render(display)
            except RenderError as exc:
                state.last_error = str(exc)
                state.consecutive_failures += 1
                outcome.reason = str(exc)
                self._save_state()
                await self._notify(outcome)
                return outcome
            except Exception as exc:  # noqa: BLE001
                state.last_error = f"{type(exc).__name__}: {exc}"
                state.consecutive_failures += 1
                outcome.reason = state.last_error
                log.exception("[%s] render failed", display_id)
                self._save_state()
                await self._notify(outcome)
                return outcome

            outcome.render_s = result.duration_s

            process_started = time.perf_counter()
            frame = process(result.image, self._pipeline_options(display))
            outcome.process_s = time.perf_counter() - process_started
            outcome.frame = frame

            state.render_count += 1
            state.last_render_at = _now()
            state.last_error = ""
            state.consecutive_failures = 0

            if display.config.render.debug_artifacts:
                self._write_debug(display, result.image, frame)

            # Lint gate: a frame that failed a hard check is not worth an e-ink
            # refresh, and on a battery panel a bad frame persists for hours.
            if self.config.block_on_lint_error and not frame.lint.ok and not force:
                issues = "; ".join(i.message for i in frame.lint.errors)
                outcome.skipped = True
                outcome.reason = f"blocked by lint: {issues}"
                state.skip_count += 1
                state.last_error = outcome.reason
                outcome.total_s = time.perf_counter() - started
                self._save_state()
                log.warning(outcome.describe())
                await self._notify(outcome)
                return outcome

            unchanged = frame.checksum == state.last_checksum
            # A pull transport can only skip if the frame is genuinely available
            # to serve. Skipping when the store is empty would leave a sleeping
            # panel fetching 404s.
            transport_for_skip = self._transports.get(display_id)
            servable = (
                transport_for_skip is None
                or transport_for_skip.pushes
                or display_id in self.frames
            )
            if unchanged and servable and display.config.schedule.skip_unchanged and not force:
                outcome.ok = True
                outcome.skipped = True
                outcome.reason = "frame unchanged"
                state.skip_count += 1
                outcome.total_s = time.perf_counter() - started
                self._save_state()
                await self._notify(outcome)
                return outcome

            full_refresh = force or self._needs_full_refresh(display, state)
            outcome.full_refresh = full_refresh

            if deliver:
                context = DeliveryContext(
                    display=display,
                    config=self.config,
                    full_refresh=full_refresh,
                    sequence=state.sequence,
                    trigger=trigger,
                    services=self.services(),
                )
                transport = self._transports.get(display_id)
                if transport is None:
                    transport = get_transport(
                        display.config.transport.type, _transport_options(display.config)
                    )
                    await transport.start()
                    self._transports[display_id] = transport
                delivery = await transport.deliver(frame, context)
                outcome.delivery = delivery
                outcome.ok = delivery.ok
                if delivery.ok:
                    state.last_checksum = frame.checksum
                    state.last_delivery_at = _now()
                    state.sequence += 1
                    state.frames_since_full = 0 if full_refresh else state.frames_since_full + 1
                else:
                    state.last_error = delivery.detail
                    outcome.reason = delivery.detail
            else:
                outcome.ok = True
                outcome.reason = "render only (delivery skipped)"

            outcome.total_s = time.perf_counter() - started
            self._save_state()
            log.info(outcome.describe())
            await self._notify(outcome)
            return outcome

    async def render_all(
        self, trigger: str = "manual", force: bool = False
    ) -> list[RenderOutcome]:
        """Render every enabled display concurrently.

        ``force`` is passed through to each display, so a service-wide "redraw
        everything now" really does bypass the unchanged-checksum shortcut.
        """
        return list(
            await asyncio.gather(
                *(
                    self.render(d.id, trigger=trigger, force=force)
                    for d in self.config.enabled_displays
                )
            )
        )

    async def probe(self, display_id: str) -> DeliveryResult:
        display = self.config.display(display_id).resolved()
        transport = self._transports.get(display_id) or get_transport(
            display.config.transport.type, _transport_options(display.config)
        )
        return await transport.probe(
            DeliveryContext(display=display, config=self.config, services=self.services())
        )

    # -------------------------------------------------------------- helpers --

    def _pipeline_options(self, display: ResolvedDisplay) -> PipelineOptions:
        image = display.config.image
        return PipelineOptions(
            width=display.width,
            height=display.height,
            scheme=display.color_scheme,
            dpi=display.dpi,
            rotation=display.rotation,
            fit=FitMode(image.fit),
            dither=DitherMode(image.dither),
            serpentine=image.serpentine,
            exposure=image.exposure,
            contrast=image.contrast,
            gamma=image.gamma,
            saturation=image.saturation,
            sharpen=image.sharpen,
            black_level=image.black_level,
            white_level=image.white_level,
            invert=image.invert,
            frame_format=FrameFormat(display.frame_format),
            pack_options=display.config.pack.to_options(),
            palette_overrides=dict(image.palette_overrides),
            lint_thresholds=display.config.lint.to_thresholds(),
        )

    def _needs_full_refresh(self, display: ResolvedDisplay, state: DisplayState) -> bool:
        cadence = display.full_refresh_every
        if not display.profile.supports_partial:
            return True
        if cadence <= 0:
            return False
        return state.frames_since_full >= cadence

    def _write_debug(self, display: ResolvedDisplay, raw: Any, frame: Frame) -> None:
        directory = self.data_dir / "debug" / display.id
        directory.mkdir(parents=True, exist_ok=True)
        raw.save(directory / "screenshot.png")
        frame.preview.save(directory / "frame.png")
        (directory / "lint.json").write_text(
            json.dumps(
                {
                    "summary": frame.lint.summary(),
                    "metrics": frame.metrics,
                    "issues": [
                        asdict(i) | {"severity": i.severity.value}
                        for i in frame.lint.issues
                    ],
                },
                indent=2,
            )
        )

    # ---------------------------------------------------------------- state --

    @property
    def _state_path(self) -> Path:
        return self.data_dir / "state.json"

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            raw = json.loads(self._state_path.read_text())
            self.states = {k: DisplayState(**v) for k, v in raw.items()}
        except Exception as exc:  # noqa: BLE001 - corrupt state must not be fatal
            log.warning("ignoring unreadable state file %s: %s", self._state_path, exc)
            self.states = {}

    def _save_state(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temporary = self._state_path.with_suffix(".tmp")
            serialised = {k: asdict(v) for k, v in self.states.items()}
            temporary.write_text(json.dumps(serialised, indent=2))
            temporary.replace(self._state_path)
        except OSError as exc:
            log.warning("could not persist state: %s", exc)


def _transport_options(display: Any) -> dict[str, Any]:
    data = display.transport.model_dump()
    data.pop("type", None)
    return data


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _env_int(name: str, default: int) -> int:
    import os

    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


__all__ = ["Engine", "RenderOutcome", "DisplayState", "FrameStore", "StoredFrame"]
