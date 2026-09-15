"""Application wiring: engine + scheduler + Home Assistant control surface.

Keeps the pieces decoupled — the engine knows nothing about MQTT, the scheduler
knows nothing about HTTP — and joins them here.
"""

from __future__ import annotations

import asyncio
import logging
import tomllib
from datetime import UTC, datetime
from importlib import metadata
from io import BytesIO
from pathlib import Path
from typing import Any

from .config import Config, ConfigError, DisplayConfig
from .engine import Engine, RenderOutcome
from .ha import MqttDiscovery
from .scheduling import RenderScheduler
from .store import DisplayStore
from .transports.mqtt import MqttPublisher

log = logging.getLogger(__name__)


def _read_version() -> str:
    """The single source of truth is ``pyproject.toml``'s ``[project].version``.

    An installed build reads it from package metadata; a bare checkout (this
    repository's own tests, or `pip install -e` before the egg-info exists)
    falls back to parsing `pyproject.toml` directly rather than reporting a
    made-up version.
    """
    try:
        return metadata.version("maverick-eink-dashboard")
    except metadata.PackageNotFoundError:
        pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        return data["project"]["version"]


VERSION = _read_version()


class Application:
    """The running service."""

    def __init__(self, config: Config, store: DisplayStore | None = None) -> None:
        self.config = config
        self.engine = Engine(config)
        self.scheduler = RenderScheduler(self.engine, config)
        self.discovery: MqttDiscovery | None = None
        #: The same file `load_config` resolved the displays from
        #: (`resolve_displays` in `src/maverick/store.py`), so a display changed
        #: at runtime is written back where the next start will read it.
        self.store = store or DisplayStore(config.display_store_path)
        self._started = False

    async def start(self, schedule: bool = True) -> None:
        if self._started:
            return
        await self.engine.start()

        mqtt: MqttPublisher | None = self.engine.mqtt
        if mqtt is not None:
            # The broker already holds our last will: Engine.start() registers
            # it, because paho can only attach one to a client that has not
            # connected yet.
            self.discovery = MqttDiscovery(mqtt, self.config, VERSION)
            await self.discovery.announce()
            self._subscribe_commands(mqtt)
            self.engine.add_listener(self._publish_outcome)

        if schedule:
            await self.scheduler.start()
        self._started = True

    async def stop(self) -> None:
        await self.scheduler.stop()
        if self.discovery is not None:
            await self.discovery.offline()
        await self.engine.stop()
        self._started = False

    # ----------------------------------------------------------- commands --

    def _subscribe_commands(self, mqtt: MqttPublisher) -> None:
        """Route Home Assistant button/switch presses back into the engine.

        paho delivers on its own thread, so each command is handed to the event
        loop rather than executed inline.
        """
        loop = asyncio.get_running_loop()
        topic = f"{self.config.mqtt.base_topic}/display/+/command"

        def handler(_client: Any, _userdata: Any, message: Any) -> None:
            try:
                display_id = message.topic.split("/")[-2]
                payload = message.payload.decode().strip()
            except Exception:  # noqa: BLE001
                log.warning("unparseable MQTT command on %s", message.topic)
                return
            asyncio.run_coroutine_threadsafe(self.handle_command(display_id, payload), loop)

        mqtt.subscribe(topic, handler)
        log.info("listening for commands on %s", topic)

    async def handle_command(self, display_id: str, command: str) -> None:
        log.info("[%s] command: %s", display_id, command)
        try:
            if command in ("refresh", "PRESS", "press"):
                await self.engine.render(display_id, trigger="button")
            elif command == "full_refresh":
                await self.engine.render(display_id, trigger="button", force=True)
            elif command in ("schedule_on", "schedule_off"):
                self.scheduler.set_enabled(display_id, command == "schedule_on")
                await self._publish_state(display_id)
            else:
                log.warning("[%s] unknown command %r", display_id, command)
        except KeyError:
            log.warning("command for unknown display %r", display_id)
        except Exception:  # noqa: BLE001
            log.exception("[%s] command %r failed", display_id, command)

    # -------------------------------------------------------------- state --

    async def _publish_outcome(self, outcome: RenderOutcome) -> None:
        if self.discovery is None:
            return
        await self._publish_state(outcome.display_id, outcome)
        if outcome.frame is not None and not outcome.skipped:
            buffer = BytesIO()
            outcome.frame.preview.save(buffer, format="PNG")
            await self.discovery.publish_image(outcome.display_id, buffer.getvalue())

    async def _publish_state(
        self, display_id: str, outcome: RenderOutcome | None = None
    ) -> None:
        if self.discovery is None:
            return
        state = self.engine.states.get(display_id)
        if state is None:
            return

        if outcome is not None and not outcome.ok and not outcome.skipped:
            status = "error"
        elif state.consecutive_failures:
            status = "error"
        elif outcome is not None and outcome.skipped:
            status = "unchanged"
        else:
            status = "ok"

        ink = None
        if outcome is not None and outcome.frame is not None:
            ink = round(outcome.frame.metrics.get("coverage.ink", 0.0) * 100, 1)

        payload: dict[str, Any] = {
            "status": status,
            "problem": bool(state.consecutive_failures),
            "schedule_enabled": self.scheduler.schedule_enabled.get(display_id, True),
            "last_render_at": state.last_render_at or None,
            "last_delivery_at": state.last_delivery_at or None,
            "sequence": state.sequence,
            "render_count": state.render_count,
            "skip_count": state.skip_count,
            "error": state.last_error or None,
            "render_duration": round(outcome.total_s, 2) if outcome else None,
            "ink_coverage": ink,
            "lint": outcome.frame.lint.summary() if outcome and outcome.frame else None,
            "trigger": outcome.trigger if outcome else None,
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        await self.discovery.publish_state(display_id, payload)

    # ---------------------------------------------------- display lifecycle --

    async def add_display(self, display: DisplayConfig) -> None:
        """Start rendering a display that was not configured a moment ago.

        The four steps are in the order that leaves nothing pointing at
        something that does not exist yet: the engine registers it (so the
        scheduler's job has a display to render and the config carries it),
        the scheduler gives it a timeline, discovery makes it a Home Assistant
        device, and the store writes it down.
        """
        # Everything that can reject the display outright happens before any of
        # it is applied: half an added display is worse than none.
        self.scheduler.check(display)
        await self.engine.register_display(display)
        self.scheduler.add_display(display)
        await self.scheduler.refresh_state_watch()
        if self.discovery is not None:
            await self.discovery.announce_display(display)
        self._save_displays()

    async def update_display(self, display_id: str, display: DisplayConfig) -> None:
        """Apply a new config to a running display.

        A new id is a rename, and nothing it appears in can be moved in place —
        the MQTT topics, the frame filenames and the state key all carry it — so
        it is done as a removal and an addition rather than pretending otherwise.
        """
        if display.id != display_id:
            # Checked here rather than left to `register_display`, which would
            # only find it once the old display had already been removed.
            if any(d.id == display.id for d in self.config.displays):
                raise ConfigError(f"duplicate display id {display.id!r}")
            await self.remove_display(display_id)
            await self.add_display(display)
            return
        self.scheduler.check(display)
        await self.engine.update_display(display)
        self.scheduler.add_display(display)
        await self.scheduler.refresh_state_watch()
        if self.discovery is not None:
            # Re-announcing overwrites the retained discovery payloads, which is
            # how a renamed panel or a changed model reaches Home Assistant.
            await self.discovery.announce_display(display)
        self._save_displays()

    async def remove_display(self, display_id: str) -> None:
        """Stop rendering a display and take it out of Home Assistant.

        Torn down in the same order it was built, so nothing is left rendering
        for a display the file no longer has.
        """
        self.config.display(display_id)  # KeyError for an id nothing has, as update does
        await self.engine.unregister_display(display_id)
        self.scheduler.remove_display(display_id)
        await self.scheduler.refresh_state_watch()
        if self.discovery is not None:
            await self.discovery.remove_display(display_id)
        self._save_displays()

    def _save_displays(self) -> None:
        """Write the displays back to the store, and say so loudly if it fails.

        The runtime change has already happened by this point and is not undone:
        a panel that is rendering correctly should keep rendering. But a service
        whose displays do not match its file would silently lose the change at
        the next restart, so the failure is raised as well as logged, for the
        caller to report.
        """
        try:
            self.store.save(self.config.displays)
        except OSError as exc:
            log.error(
                "could not write the display store %s: %s. The change is live now, "
                "but it will be lost when Maverick restarts.",
                self.store.path,
                exc,
            )
            raise

    # ------------------------------------------------------------- render --

    async def render(self, display_id: str, trigger: str = "api", force: bool = False):
        return await self.engine.render(display_id, trigger=trigger, force=force)

    async def render_all(self, trigger: str = "api", force: bool = False):
        return await self.engine.render_all(trigger=trigger, force=force)


__all__ = ["Application", "VERSION"]
