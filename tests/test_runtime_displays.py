"""A display can be added, changed and removed while the service runs.

Every per-display resource used to be built in a start-up loop: the engine's
transport, the scheduler's job, the MQTT device. Changing one meant a restart,
which relaunches Chromium and — with ``render_on_start`` on by default — redraws
every panel at once, so editing the kitchen display cost the bedroom one a
refresh.

These tests drive the four steps through :class:`~maverick.app.Application`,
which is what composes them, on the ``make_engine`` doubles: ``FakeTransport``
records deliveries and ``FakeRenderer`` stands in for Chromium, so nothing here
opens a browser, a broker or a socket. The thing they all check, one way or
another, is that the *rest* of the service does not notice: the browser keeps
running, the other display keeps its state, and an in-flight render finishes
before its transport is taken away.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest
from conftest import DISPLAY_ID, PANEL_SIZE, FakeRenderer
from PIL import Image
from test_mqtt_will import FakeClient, fake_clients  # noqa: F401 - used by name as a fixture

from maverick import engine as engine_module
from maverick.app import Application
from maverick.config import Config, DisplayConfig
from maverick.scheduling import RenderScheduler

# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #

def display(display_id: str, **overrides: Any) -> DisplayConfig:
    """A display the doubles can drive: small, mono, PNG, ``fake`` transport."""
    width, height = PANEL_SIZE
    return DisplayConfig.model_validate(
        {
            "id": display_id,
            "name": display_id.title(),
            "panel": "generic-mono",
            "width": width,
            "height": height,
            "frame_format": "png",
            "transport": {"type": "fake"},
            **overrides,
        }
    )


class GatedRenderer(FakeRenderer):
    """A renderer that stops inside ``render`` until it is let go.

    Which is the only way to observe the ordering that matters: whether removing
    a display waits for the render already in flight.
    """

    def __init__(self, image: Image.Image) -> None:
        super().__init__(image)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def render(self, display):  # type: ignore[no-untyped-def]
        self.entered.set()
        await self.release.wait()
        return await super().render(display)


class FakeHomeAssistant:
    """Enough of ``HomeAssistantClient`` for the state watch: it records and waits."""

    def __init__(self) -> None:
        self.watched: list[set[str]] = []

    async def watch_states(self, entity_ids, callback, stop=None) -> None:  # noqa: ANN001
        del callback, stop
        self.watched.append(set(entity_ids))
        await asyncio.Event().wait()  # the real one runs until cancelled

    async def close(self) -> None:
        """`Engine.stop` closes whatever client it holds."""


@dataclass
class AppHarness:
    app: Application
    renderer: FakeRenderer

    @property
    def engine(self):  # type: ignore[no-untyped-def]
        return self.app.engine

    @property
    def scheduler(self) -> RenderScheduler:
        return self.app.scheduler

    def transport(self, display_id: str) -> Any:
        """The transport instance the engine built for this display."""
        return self.engine._transports[display_id]

    def job_ids(self) -> set[str]:
        return {job.display_id for job in self.scheduler.jobs() if job.next_run is not None}


def _spy(method, calls: list[str]):
    """Wrap one instance's async method so a test can see that it was called.

    ``FakeTransport`` records its own ``stop``, but a real transport does not,
    and the display being removed here uses ``http_pull`` — the one that fills
    the frame store, which is the other half of what the test checks.
    """

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        calls.append("stopped")
        return await method(*args, **kwargs)

    return wrapper


async def drain(scheduler: RenderScheduler) -> None:
    """Run whatever the scheduler queued for later — a ``render_on_start``, say."""
    while pending := [task for task in scheduler._pending.values() if not task.done()]:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture
async def make_app(minimal_config: Config, monkeypatch, legible_image):
    """A started :class:`Application` with the renderer swapped out.

    ``render_on_start`` is off for the display the config starts with, so every
    render the tests count is one they asked for.
    """
    started: list[Application] = []

    async def _make(
        *,
        image: Image.Image | None = None,
        renderer: FakeRenderer | None = None,
        mqtt: bool = False,
    ) -> AppHarness:
        minimal_config.display(DISPLAY_ID).schedule.render_on_start = False
        if mqtt:
            minimal_config.mqtt.enabled = True
        renderer = renderer or FakeRenderer(image or legible_image)
        monkeypatch.setattr(
            engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer
        )
        app = Application(minimal_config)
        await app.start()
        started.append(app)
        return AppHarness(app=app, renderer=renderer)

    yield _make

    for app in started:
        await app.stop()


@pytest.fixture
def no_browser_stop(monkeypatch):
    """Count every call to ``BrowserPool.stop``, and stop it from doing anything.

    Dropping one display's contexts must never take Chromium down with it: the
    browser is shared, it costs seconds to relaunch, and every other panel is
    waiting on it.
    """
    from maverick.render.browser import BrowserPool

    stops: list[str] = []

    async def counted(self: BrowserPool) -> None:
        stops.append("stop")

    monkeypatch.setattr(BrowserPool, "stop", counted)
    return stops


# --------------------------------------------------------------------------- #
# Adding
# --------------------------------------------------------------------------- #

async def test_add_display_starts_a_transport_a_job_and_one_render(make_app) -> None:
    harness = await make_app()

    await harness.app.add_display(display("study", schedule={"every": "5m"}))
    await drain(harness.scheduler)

    assert harness.transport("study").started, "the transport should be started, not just built"
    assert harness.job_ids() == {"study"}, "an interval schedule should have a job"
    assert len(harness.transport("study").deliveries) == 1, (
        "render_on_start should render the new display once, and only it"
    )
    assert harness.renderer.calls == 1, "no other display should have been redrawn"
    assert harness.engine.states["study"].render_count == 1


async def test_add_display_writes_the_store(make_app) -> None:
    harness = await make_app()

    await harness.app.add_display(display("study"))
    await drain(harness.scheduler)

    stored = {d.id for d in harness.app.store.load()}
    assert stored == {DISPLAY_ID, "study"}, "the store is what the next start reads"


async def test_add_display_refuses_a_duplicate_id(make_app) -> None:
    """`Config._unique_ids` has to run, which means assigning the list, not appending."""
    harness = await make_app()

    with pytest.raises(ValueError, match="duplicate display id"):
        await harness.app.add_display(display(DISPLAY_ID))

    assert [d.id for d in harness.app.config.displays] == [DISPLAY_ID]
    assert harness.engine.states.keys() == {DISPLAY_ID}


async def test_a_manual_only_display_gets_no_job(make_app) -> None:
    harness = await make_app()

    await harness.app.add_display(display("study", schedule={"render_on_start": False}))

    assert harness.job_ids() == set(), "nothing to schedule without `every` or `cron`"
    assert harness.transport("study").started, "but it still delivers when asked"


# --------------------------------------------------------------------------- #
# Updating
# --------------------------------------------------------------------------- #

async def test_update_swaps_the_transport_without_touching_the_browser(
    make_app, no_browser_stop
) -> None:
    harness = await make_app()
    original = harness.transport(DISPLAY_ID)

    await harness.app.update_display(
        DISPLAY_ID, display(DISPLAY_ID, transport={"type": "http_pull"})
    )

    assert original.started is False, "the old transport instance must be stopped"
    assert harness.transport(DISPLAY_ID) is not original
    assert harness.transport(DISPLAY_ID).name == "http_pull"
    assert no_browser_stop == [], "Chromium is shared; a display change must not stop it"

    # And back, where `started` is observable on the new instance too.
    await harness.app.update_display(DISPLAY_ID, display(DISPLAY_ID))
    assert harness.transport(DISPLAY_ID).started is True
    assert no_browser_stop == []


async def test_update_keeps_the_state_entry_and_the_stored_frame(make_app) -> None:
    """Counters describe the panel, not the config: a new dither clears no ghosting."""
    harness = await make_app()
    await harness.app.add_display(display("study", transport={"type": "http_pull"}))
    await drain(harness.scheduler)

    before = harness.engine.states["study"]
    before.frames_since_full = 4
    assert "study" in harness.engine.frames

    await harness.app.update_display(
        "study", display("study", transport={"type": "http_pull"}, image={"dither": "none"})
    )

    assert harness.engine.states["study"] is before
    assert harness.engine.states["study"].frames_since_full == 4
    assert "study" in harness.engine.frames, (
        "a pull device that wakes before the next render still needs a frame"
    )
    assert harness.app.config.display("study").image.dither == "none"


async def test_update_reschedules_the_job(make_app) -> None:
    harness = await make_app()
    await harness.app.add_display(
        display("study", schedule={"every": "5m", "render_on_start": False})
    )
    assert harness.job_ids() == {"study"}

    await harness.app.update_display(
        "study", display("study", schedule={"enabled": False, "render_on_start": False})
    )

    assert harness.job_ids() == set(), "a disabled schedule leaves no job behind"
    assert harness.scheduler.schedule_enabled["study"] is False


async def test_update_of_an_unknown_display_is_a_key_error(make_app) -> None:
    harness = await make_app()

    with pytest.raises(KeyError):
        await harness.app.update_display("nowhere", display("nowhere"))


async def test_a_new_id_moves_the_display(make_app) -> None:
    """An id is in the topics, the filenames and the state key: a rename is a move."""
    harness = await make_app()
    await harness.app.add_display(display("study", schedule={"render_on_start": False}))

    await harness.app.update_display(
        "study", display("library", schedule={"render_on_start": False})
    )

    assert [d.id for d in harness.app.config.displays] == [DISPLAY_ID, "library"]
    assert "study" not in harness.engine._transports
    assert harness.transport("library").started
    assert {d.id for d in harness.app.store.load()} == {DISPLAY_ID, "library"}


async def test_a_rename_onto_a_used_id_is_refused_before_anything_moves(make_app) -> None:
    harness = await make_app()
    await harness.app.add_display(display("study", schedule={"render_on_start": False}))

    with pytest.raises(ValueError, match="duplicate display id"):
        await harness.app.update_display("study", display(DISPLAY_ID))

    assert [d.id for d in harness.app.config.displays] == [DISPLAY_ID, "study"]
    assert harness.transport("study").started, "the display being renamed must survive"


async def test_a_schedule_that_cannot_be_built_changes_nothing(make_app) -> None:
    """A cron expression is only parsed by APScheduler, long after the config loaded."""
    harness = await make_app()

    with pytest.raises(ValueError, match="invalid cron expression"):
        await harness.app.add_display(display("study", schedule={"cron": "not a cron"}))

    assert [d.id for d in harness.app.config.displays] == [DISPLAY_ID]
    assert "study" not in harness.engine._transports
    assert "study" not in harness.engine.states
    assert not harness.app.store.exists(), "a rejected display is never written down"


# --------------------------------------------------------------------------- #
# Removing
# --------------------------------------------------------------------------- #

async def test_remove_display_takes_everything_with_it(make_app, no_browser_stop) -> None:
    harness = await make_app()
    await harness.app.add_display(
        display("study", transport={"type": "http_pull"}, schedule={"every": "5m"})
    )
    await drain(harness.scheduler)

    transport = harness.transport("study")
    stopped: list[str] = []
    transport.stop = _spy(transport.stop, stopped)
    frames_dir = harness.engine.data_dir / "frames"
    assert sorted(p.name for p in frames_dir.glob("study.*")) == [
        "study.frame",
        "study.json",
        "study.preview.png",
        "study.screenshot.png",
    ]

    await harness.app.remove_display("study")

    assert harness.job_ids() == set(), "the job has to go, or it renders a display that is gone"
    assert stopped == ["stopped"], "the transport has to be stopped, not just dropped"
    assert "study" not in harness.engine._transports
    assert "study" not in harness.engine.states
    assert "study" not in harness.engine.frames
    assert list(frames_dir.glob("study.*")) == []
    assert [d.id for d in harness.app.config.displays] == [DISPLAY_ID]
    assert {d.id for d in harness.app.store.load()} == {DISPLAY_ID}
    assert no_browser_stop == [], "removing a display must not stop the shared browser"

    saved = json.loads((harness.engine.data_dir / "state.json").read_text())
    assert "study" not in saved, "the removed display must not come back on a restart"


async def test_remove_of_an_unknown_display_is_a_key_error(make_app) -> None:
    harness = await make_app()

    with pytest.raises(KeyError):
        await harness.app.remove_display("nowhere")


async def test_remove_display_retracts_its_discovery(make_app, fake_clients) -> None:  # noqa: F811
    harness = await make_app(mqtt=True)
    await harness.app.add_display(display("study"))
    await drain(harness.scheduler)
    client: FakeClient = fake_clients[0]

    announced = {
        topic for topic, payload, _retain in client.published
        if topic.startswith("homeassistant/") and "maverick_study" in topic and payload
    }
    assert announced, "the display should have been announced before it is retracted"

    await harness.app.remove_display("study")

    retracted = {
        topic for topic, payload, retain in client.published
        if "maverick_study" in topic and payload == "" and retain
    }
    assert retracted == announced, (
        "every discovery topic must be retracted with an empty retained payload, "
        "or the entities linger in Home Assistant forever"
    )


async def test_remove_waits_for_a_render_in_flight(make_app, legible_image) -> None:
    renderer = GatedRenderer(legible_image)
    harness = await make_app(renderer=renderer)
    transport = harness.transport(DISPLAY_ID)

    rendering = asyncio.create_task(harness.engine.render(DISPLAY_ID))
    await renderer.entered.wait()
    assert harness.engine.is_rendering(DISPLAY_ID) is True

    removing = asyncio.create_task(harness.app.remove_display(DISPLAY_ID))
    for _ in range(3):
        await asyncio.sleep(0)
    assert not removing.done(), "removal must wait on the render lock, not barge in"

    renderer.release.set()
    await removing
    outcome = await rendering

    assert outcome.ok, "the render in flight should have finished normally"
    assert len(transport.deliveries) == 1, "and delivered before its transport was stopped"
    assert transport.started is False
    assert harness.engine.is_rendering(DISPLAY_ID) is False


async def test_a_render_queued_behind_a_removal_does_not_resurrect_it(
    make_app, legible_image
) -> None:
    """The display is looked up inside the lock, so the queued render finds nothing."""
    renderer = GatedRenderer(legible_image)
    harness = await make_app(renderer=renderer)

    first = asyncio.create_task(harness.engine.render(DISPLAY_ID))
    await renderer.entered.wait()
    # The lock is handed out in the order it was asked for, so the removal has
    # to queue ahead of the second render for this to be the case under test.
    removing = asyncio.create_task(harness.app.remove_display(DISPLAY_ID))
    for _ in range(3):
        await asyncio.sleep(0)
    queued = asyncio.create_task(harness.engine.render(DISPLAY_ID))
    for _ in range(3):
        await asyncio.sleep(0)

    renderer.release.set()
    await first
    await removing
    with pytest.raises(KeyError):
        await queued

    assert harness.engine.states == {}, "no state entry may be recreated after the removal"


# --------------------------------------------------------------------------- #
# State triggers
# --------------------------------------------------------------------------- #

async def test_refresh_state_watch_rebuilds_the_watched_set(make_app) -> None:
    harness = await make_app()
    home_assistant = FakeHomeAssistant()
    harness.engine._ha = home_assistant

    await harness.app.add_display(
        display("study", schedule={"on_change": ["sensor.a"], "render_on_start": False})
    )
    await asyncio.sleep(0)
    first_task = harness.scheduler._watch_task
    assert home_assistant.watched == [{"sensor.a"}]

    await harness.app.add_display(
        display("porch", schedule={"on_change": ["sensor.b"], "render_on_start": False})
    )
    await asyncio.sleep(0)
    assert home_assistant.watched[-1] == {"sensor.a", "sensor.b"}
    assert first_task.cancelled(), "the previous subscription must not be left running"

    await harness.app.remove_display("study")
    await asyncio.sleep(0)
    assert home_assistant.watched[-1] == {"sensor.b"}


async def test_refresh_state_watch_without_home_assistant_is_a_no_op(make_app, caplog) -> None:
    harness = await make_app()
    assert harness.engine.ha is None

    with caplog.at_level("INFO"):
        await harness.scheduler.refresh_state_watch()

    assert harness.scheduler._watch_task is None
    assert "on_change triggers stay inactive" in caplog.text


# --------------------------------------------------------------------------- #
# Browser contexts
# --------------------------------------------------------------------------- #

class RecordingContext:
    """A browser context that records being closed, and can refuse to."""

    def __init__(self, key: str, closed: list[str], fails: bool = False) -> None:
        self.key = key
        self._closed = closed
        self._fails = fails

    async def close(self) -> None:
        self._closed.append(self.key)
        if self._fails:
            raise RuntimeError("target page, context or browser has been closed")


#: What `DashboardRenderer.render` builds as a context key, which is
#: `f"{display.id}:{viewport}:{supersample}:{hash(scripts)}"`
#: (`src/maverick/render/dashboard.py`). The prefix rule has to survive the
#: neighbour whose id merely starts with the same letters.
CONTEXT_KEYS = [
    "kitchen:(800, 480):2:1234",
    "kitchen:(480, 800):2:1234",
    "kitchen-annexe:(800, 480):2:1234",
    "hallway:(800, 480):2:1234",
]


async def test_drop_contexts_for_takes_one_displays_contexts_and_no_others() -> None:
    from maverick.render.browser import BrowserPool

    pool = BrowserPool()
    closed: list[str] = []
    pool._contexts = {key: RecordingContext(key, closed) for key in CONTEXT_KEYS}

    dropped = await pool.drop_contexts_for("kitchen")

    assert dropped == 2, "both of the kitchen's geometries, and nothing else"
    assert closed == CONTEXT_KEYS[:2]
    assert set(pool._contexts) == {"kitchen-annexe:(800, 480):2:1234", "hallway:(800, 480):2:1234"}


async def test_a_context_that_will_not_close_does_not_abort_the_rest() -> None:
    """Closing is cleanup; a dead Chromium must not strand the display's removal."""
    from maverick.render.browser import BrowserPool

    pool = BrowserPool()
    closed: list[str] = []
    pool._contexts = {
        CONTEXT_KEYS[0]: RecordingContext(CONTEXT_KEYS[0], closed, fails=True),
        CONTEXT_KEYS[1]: RecordingContext(CONTEXT_KEYS[1], closed),
    }

    dropped = await pool.drop_contexts_for("kitchen")

    assert dropped == 2
    assert closed == CONTEXT_KEYS[:2], "the second one still had to be closed"
    assert pool._contexts == {}


# --------------------------------------------------------------------------- #
# Previewing
# --------------------------------------------------------------------------- #

async def test_render_candidate_touches_nothing(make_app, blank_image) -> None:
    harness = await make_app(image=blank_image)
    await harness.engine.render(DISPLAY_ID)  # so there is state on disk to compare against
    state_path = harness.engine.data_dir / "state.json"
    state_before = state_path.read_bytes()
    frames_before = sorted(p.name for p in (harness.engine.data_dir / "frames").glob("*"))

    outcome = await harness.engine.render_candidate(display("candidate"))

    assert outcome.ok, "a preview reports lint findings; it does not fail on them"
    assert outcome.frame is not None
    assert outcome.frame.lint.errors, "a blank frame should still be described as blank"
    assert outcome.delivery is None, "a preview delivers nothing"
    assert harness.engine.states.keys() == {DISPLAY_ID}
    assert "candidate" not in harness.engine.frames
    assert state_path.read_bytes() == state_before
    assert sorted(p.name for p in (harness.engine.data_dir / "frames").glob("*")) == frames_before
    assert list(harness.engine.data_dir.rglob("*candidate*")) == []


async def test_render_candidate_works_for_a_registered_display_too(make_app) -> None:
    """The Preview button previews an edit to a display that is already running."""
    harness = await make_app()
    transport = harness.transport(DISPLAY_ID)

    outcome = await harness.engine.render_candidate(
        display(DISPLAY_ID, image={"dither": "none"})
    )

    assert outcome.ok
    assert outcome.display_id == DISPLAY_ID, "the outcome names the display, not the throwaway id"
    assert transport.deliveries == [], "nothing reaches the panel from a preview"
    assert DISPLAY_ID not in harness.engine.frames
    assert harness.engine.states[DISPLAY_ID].render_count == 0
