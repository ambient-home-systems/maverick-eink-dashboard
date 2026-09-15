"""Render timelines and triggers.

Three ways a render starts, and all three land in the same place:

1. **A schedule** — ``every: 5m`` or a cron expression. Cron matters more than
   it looks for e-ink: "every morning at 06:00" and "on the hour during the day"
   are the natural way to drive a panel, because a refresh is visible and
   slightly disruptive, so you want it to happen on a human rhythm.
2. **A state change** — ``on_change: [sensor.x]`` subscribes to Home Assistant's
   event stream, debounced. This is how a panel follows its data.
3. **An explicit trigger** — a button press in Home Assistant, a REST call, a
   webhook. Handled elsewhere; the scheduler just exposes the same entry point.

Quiet hours exist because these panels end up in bedrooms. A full refresh
flashes the whole panel black and white several times.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dt_time

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..config import Config, DisplayConfig, ScheduleConfig
from ..engine import Engine

log = logging.getLogger(__name__)


def parse_quiet_hours(window: str) -> tuple[dt_time, dt_time]:
    start_text, end_text = window.split("-")
    start_h, start_m = (int(p) for p in start_text.split(":"))
    end_h, end_m = (int(p) for p in end_text.split(":"))
    return dt_time(start_h, start_m), dt_time(end_h, end_m)


def in_quiet_hours(window: str | None, now: datetime | None = None) -> bool:
    """True if ``now`` falls inside the window, which may wrap midnight."""
    if not window:
        return False
    start, end = parse_quiet_hours(window)
    current = (now or datetime.now()).time()
    if start <= end:
        return start <= current < end
    # Wraps midnight: 23:00-06:30 means "late evening or early morning".
    return current >= start or current < end


@dataclass
class ScheduledJob:
    display_id: str
    kind: str
    detail: str
    next_run: datetime | None = None


class RenderScheduler:
    """Drives renders from schedules and Home Assistant state changes."""

    def __init__(self, engine: Engine, config: Config) -> None:
        self._engine = engine
        self._config = config
        self._scheduler = AsyncIOScheduler(timezone=None)
        self._debounce: dict[str, asyncio.TimerHandle] = {}
        self._pending: dict[str, asyncio.Task] = {}
        self._watch_task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        #: display_id -> enabled, so a Home Assistant switch can pause a panel
        #: without editing the config file.
        self.schedule_enabled: dict[str, bool] = {}

    # ------------------------------------------------------------ lifecycle --

    async def start(self) -> None:
        for display in self._config.enabled_displays:
            self.add_display(display, render_on_start=False)

        self._scheduler.start()
        await self._start_state_watch()

        for display in self._config.enabled_displays:
            if display.schedule.render_on_start:
                self._render_now(display.id)

    async def stop(self) -> None:
        self._stop.set()
        for handle in self._debounce.values():
            handle.cancel()
        self._debounce.clear()
        for task in self._pending.values():
            task.cancel()
        if self._watch_task is not None:
            self._watch_task.cancel()
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    # ----------------------------------------------------- display lifecycle --

    def check(self, display: DisplayConfig) -> None:
        """Raise if this display cannot be scheduled, without scheduling it.

        `every` and `cron` are validated as a pair when the config loads
        (`ScheduleConfig` in `src/maverick/config.py`), but the cron *expression*
        is only parsed here, by APScheduler. A caller applying a display to the
        running service asks first, so a typo in one is a rejected change rather
        than a display that is half added.
        """
        self._build_trigger(display.schedule)

    def add_display(self, display: DisplayConfig, render_on_start: bool = True) -> None:
        """Schedule one display, as `start()` does for each at startup.

        Also the update path: the job id is per display and `replace_existing`
        is set, so re-adding a display swaps its timeline without a gap. Any
        debounce or render still pending under the old schedule is dropped
        first, because it was queued against settings that no longer apply.

        `render_on_start=False` is for `start()` itself, which renders every
        display once afterwards rather than one at a time.
        """
        self.remove_display(display.id)
        self.schedule_enabled[display.id] = display.schedule.enabled

        if not display.enabled:
            log.info("[%s] disabled; not scheduled", display.id)
            return
        if not display.schedule.enabled:
            log.info("[%s] schedule disabled", display.id)
        else:
            trigger = self._build_trigger(display.schedule)
            if trigger is not None:
                self._scheduler.add_job(
                    self._run,
                    trigger,
                    args=[display.id, "schedule"],
                    id=self._job_id(display.id),
                    max_instances=1,
                    coalesce=True,      # a backlog after a pause is pointless
                    misfire_grace_time=60,
                    replace_existing=True,
                )
            log.info("[%s] scheduled: %s", display.id, self._describe(display.schedule))

        if render_on_start and display.schedule.render_on_start:
            self._render_now(display.id)

    def remove_display(self, display_id: str) -> None:
        """Forget one display's job, its pause flag and anything queued for it.

        Idempotent, and safe for a display that was never added: it is called
        for its own sake when a display is removed, and again by `add_display`
        to clear the previous schedule.
        """
        with contextlib.suppress(JobLookupError):
            self._scheduler.remove_job(self._job_id(display_id))
        if (handle := self._debounce.pop(display_id, None)) is not None:
            handle.cancel()
        if (task := self._pending.pop(display_id, None)) is not None:
            task.cancel()
        self.schedule_enabled.pop(display_id, None)

    async def refresh_state_watch(self) -> None:
        """Rebuild the `on_change` subscription from the displays as they are now.

        One websocket watches every entity any display names, so a display added,
        changed or removed means a new set and a new subscription: there is
        nothing to add an entity to. Cancelling and re-subscribing costs one
        reconnect, which is what Home Assistant's own restarts cost anyway
        (`HomeAssistantClient.watch_states`).
        """
        if self._watch_task is not None:
            self._watch_task.cancel()
            self._watch_task = None
        if self._engine.ha is None:
            log.info(
                "no Home Assistant client; on_change triggers stay inactive until "
                "an account is linked"
            )
            return
        await self._start_state_watch()

    @staticmethod
    def _job_id(display_id: str) -> str:
        return f"render:{display_id}"

    def _render_now(self, display_id: str) -> None:
        """Queue one render outside the timeline, and keep hold of the task."""
        task = asyncio.create_task(self._run(display_id, "startup"))
        self._pending[display_id] = task
        task.add_done_callback(lambda finished: self._forget_pending(display_id, finished))

    def _forget_pending(self, display_id: str, task: asyncio.Task) -> None:
        """Drop a finished task, unless a newer one has already taken its place."""
        if self._pending.get(display_id) is task:
            del self._pending[display_id]

    # --------------------------------------------------------------- jobs --

    def _build_trigger(self, schedule: ScheduleConfig) -> CronTrigger | IntervalTrigger | None:
        if schedule.cron:
            try:
                return CronTrigger.from_crontab(schedule.cron)
            except ValueError as exc:
                raise ValueError(f"invalid cron expression {schedule.cron!r}: {exc}") from exc
        seconds = schedule.interval_seconds
        if seconds:
            return IntervalTrigger(seconds=seconds)
        return None

    @staticmethod
    def _describe(schedule: ScheduleConfig) -> str:
        parts = []
        if schedule.cron:
            parts.append(f"cron '{schedule.cron}'")
        elif schedule.every:
            parts.append(f"every {schedule.every}")
        if schedule.quiet_hours:
            parts.append(f"quiet {schedule.quiet_hours}")
        if schedule.on_change:
            parts.append(f"on change of {len(schedule.on_change)} entities")
        return ", ".join(parts) or "manual only"

    async def _run(self, display_id: str, trigger: str) -> None:
        if not self.schedule_enabled.get(display_id, True) and trigger in ("schedule", "state"):
            log.debug("[%s] schedule paused, ignoring %s trigger", display_id, trigger)
            return
        try:
            schedule = self._config.display(display_id).schedule
        except KeyError:
            # Removed between the trigger firing and this running.
            log.debug("[%s] no longer configured; ignoring %s trigger", display_id, trigger)
            return
        if trigger in ("schedule", "state") and in_quiet_hours(schedule.quiet_hours):
            log.debug("[%s] in quiet hours, skipping", display_id)
            return
        try:
            await self._engine.render(display_id, trigger=trigger)
        except Exception:  # noqa: BLE001 - a failed render must not kill the job
            log.exception("[%s] scheduled render failed", display_id)

    # ------------------------------------------------------- state triggers --

    async def _start_state_watch(self) -> None:
        watched: dict[str, list[str]] = {}
        for display in self._config.enabled_displays:
            for entity_id in display.schedule.on_change:
                watched.setdefault(entity_id, []).append(display.id)
        if not watched:
            return
        if self._engine.ha is None:
            log.warning(
                "on_change is configured for %d entities but Home Assistant is "
                "not connected; state triggers are inactive",
                len(watched),
            )
            return

        async def on_state(entity_id: str, old: dict | None, new: dict | None) -> None:
            # Attribute-only updates fire state_changed too; ignore them, or a
            # panel re-renders every time an attribute timestamp ticks.
            if old and new and old.get("state") == new.get("state"):
                return
            for display_id in watched.get(entity_id, []):
                self._debounced(display_id, entity_id)

        self._watch_task = asyncio.create_task(
            self._engine.ha.watch_states(set(watched), on_state, self._stop)
        )
        log.info("watching %d entities for state triggers", len(watched))

    def _debounced(self, display_id: str, entity_id: str) -> None:
        """Coalesce a burst of changes into a single render.

        A thermostat that reports every few seconds would otherwise queue a
        render per reading, and each one costs a panel refresh.
        """
        delay = float(self._config.display(display_id).schedule.debounce)
        loop = asyncio.get_running_loop()
        if (existing := self._debounce.pop(display_id, None)) is not None:
            existing.cancel()

        def fire() -> None:
            self._debounce.pop(display_id, None)
            task = asyncio.create_task(self._run(display_id, "state"))
            self._pending[display_id] = task
            task.add_done_callback(lambda finished: self._forget_pending(display_id, finished))

        log.debug("[%s] %s changed; render in %.0fs", display_id, entity_id, delay)
        self._debounce[display_id] = loop.call_later(delay, fire)

    # ------------------------------------------------------------ inspection --

    def jobs(self) -> list[ScheduledJob]:
        found = []
        for display in self._config.enabled_displays:
            job = self._scheduler.get_job(self._job_id(display.id))
            found.append(
                ScheduledJob(
                    display_id=display.id,
                    kind="cron" if display.schedule.cron else "interval",
                    detail=self._describe(display.schedule),
                    next_run=getattr(job, "next_run_time", None) if job else None,
                )
            )
        return found

    def set_enabled(self, display_id: str, enabled: bool) -> None:
        """Pause or resume a display's schedule at runtime."""
        self.schedule_enabled[display_id] = enabled
        log.info("[%s] schedule %s", display_id, "resumed" if enabled else "paused")

    async def trigger(self, display_id: str, reason: str = "trigger") -> None:
        await self._run(display_id, reason)


__all__ = ["RenderScheduler", "ScheduledJob", "in_quiet_hours", "parse_quiet_hours"]
