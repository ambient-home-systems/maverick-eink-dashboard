"""Several dashboards on one panel: the page list, rotation and every way in.

A display used to render exactly one dashboard. It now carries an ordered
`pages` list (`DisplayConfig`, `src/maverick/config.py`) that `Engine.render`
resolves at render time, `RenderScheduler` advances on its own timeline, and
the HTTP API, the Home Assistant Page select and the setup UI all move between.

Five things have to hold, and each fails here rather than on a panel:

* **one source of truth for what a display shows** — `dashboard` is the
  single-page shorthand and `pages` is the list; both together is a load
  error, because nothing would say which wins;
* **the page is resolved per render, not per config load** — the renderer sees
  the page that is on the panel now, whatever moved it there, which is what
  the recording renderer below asserts directly;
* **`dwell` is measured, not counted** — a page stays up for its dwell however
  many scheduled ticks pass inside it, which needs a clock the test can drive
  rather than real time;
* **a page is selected by name as well as by index**, because that is what
  Home Assistant's select sends and what an automation writes;
* **nothing here needs a browser, a broker or Home Assistant** — the renderer
  and the transport are the doubles in `conftest.py` and the MQTT client is
  the fake from `test_mqtt_will.py`, as `tests/conftest.py` promises.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import DISPLAY_ID, FakeRenderer
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from PIL import Image
from test_mqtt_will import FakeClient, fake_clients  # noqa: F401 - used by name as a fixture

from maverick import engine as engine_module
from maverick.app import Application
from maverick.config import Config, ConfigError, DisplayConfig, PageConfig, page_name_for
from maverick.engine import Engine
from maverick.scheduling import RenderScheduler
from maverick.server.api import create_app
from maverick.store import dump_display

OVERVIEW = "/lovelace-eink/overview"
CALENDAR = "/lovelace-eink/calendar"
WEATHER = "/lovelace-eink/weather"

#: Three pages, no dwell: the shape every test starts from unless it needs one.
THREE_PAGES = [{"dashboard": OVERVIEW}, {"dashboard": CALENDAR}, {"dashboard": WEATHER}]


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #


class RecordingRenderer(FakeRenderer):
    """Remembers the dashboard of every render, which is what a page *is*.

    `Engine.render` resolves the current page into `config.dashboard` before
    calling the renderer (`DisplayConfig.for_page`), so this records what the
    browser would have been pointed at.
    """

    def __init__(self, image: Image.Image) -> None:
        super().__init__(image)
        self.dashboards: list[str] = []

    async def render(self, display):  # type: ignore[no-untyped-def]
        self.dashboards.append(display.config.dashboard)
        return await super().render(display)


class FakeClock:
    """A clock a test moves by hand, in place of `datetime.now(UTC)`."""

    def __init__(self, start: datetime | None = None) -> None:
        self.moment = start or datetime(2026, 9, 16, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.moment

    def advance(self, seconds: float) -> None:
        self.moment += timedelta(seconds=seconds)


def paged(config: Config, pages: list[dict] | None, *, rotate: bool = False) -> DisplayConfig:
    """Give the config's one display a page list, as a config file would."""
    display = config.display(DISPLAY_ID)
    display.pages = pages or []
    display.rotate = rotate
    display.schedule.render_on_start = False
    return display


@pytest.fixture
async def make_paged_engine(minimal_config: Config, monkeypatch, legible_image: Image.Image):
    """A started `Engine` whose display has pages, plus the recording renderer.

    The engine is the real one — it builds the `fake` transport through the
    registry and keeps its own state — with only `DashboardRenderer` swapped
    out, exactly as `make_engine` in `conftest.py` does.
    """
    engines: list[Engine] = []

    async def _make(
        pages: list[dict] | None,
        *,
        rotate: bool = False,
        clock: FakeClock | None = None,
    ) -> tuple[Engine, RecordingRenderer]:
        paged(minimal_config, pages, rotate=rotate)
        renderer = RecordingRenderer(legible_image)
        monkeypatch.setattr(
            engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer
        )
        engine = Engine(minimal_config, clock=clock)
        await engine.start()
        engines.append(engine)
        return engine, renderer

    yield _make

    for engine in engines:
        await engine.stop()


@pytest.fixture
def paged_api(minimal_config: Config, monkeypatch, legible_image: Image.Image):
    """A started `Application` with three pages, reached over HTTP."""
    paged(minimal_config, THREE_PAGES)
    renderer = RecordingRenderer(legible_image)
    monkeypatch.setattr(
        engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer
    )
    app = Application(minimal_config)
    with TestClient(create_app(app)) as client:
        yield client, app, renderer


# --------------------------------------------------------------------------- #
# The config: the shorthand, the list, and the two together
# --------------------------------------------------------------------------- #


def test_a_display_without_pages_has_one_page_its_dashboard() -> None:
    """The shorthand is a page list of one, so nothing downstream has to ask."""
    display = DisplayConfig(id="kitchen", dashboard=CALENDAR)

    assert display.pages == []
    assert [page.dashboard for page in display.page_entries] == [CALENDAR]
    assert display.page_at(0).name == "Calendar"
    # Wrapping, not raising: a stored index outlives the config it was written
    # against (`DisplayState.page_index`, `src/maverick/engine.py`).
    assert display.page_at(7).dashboard == CALENDAR


def test_pages_are_read_in_order_with_their_own_names_and_dwell() -> None:
    display = DisplayConfig.model_validate(
        {
            "id": "kitchen",
            "pages": [
                {"dashboard": OVERVIEW, "dwell": "30m"},
                {"dashboard": CALENDAR, "name": "Week ahead"},
            ],
            "rotate": True,
        }
    )

    assert [page.name for page in display.pages] == ["Overview", "Week ahead"]
    assert [page.dwell_seconds for page in display.pages] == [1800.0, None]
    assert display.rotate is True
    assert display.page_index_for("Week ahead") == 1
    assert display.for_page(1).dashboard == CALENDAR


def test_a_dashboard_and_pages_together_are_refused() -> None:
    """Two answers to "what does this panel show" and nothing to pick between."""
    with pytest.raises(ValueError, match="set either 'dashboard' or 'pages'"):
        DisplayConfig.model_validate(
            {"id": "kitchen", "dashboard": CALENDAR, "pages": [{"dashboard": OVERVIEW}]}
        )


def test_the_default_dashboard_beside_pages_is_not_a_conflict() -> None:
    """Only a dashboard someone *set* conflicts.

    The setup UI's drawer sends every control it drew, the Dashboard box
    included, so a display that never set one must still be able to gain pages
    (`collectBody`, `src/maverick/server/static/app.js`).
    """
    display = DisplayConfig.model_validate(
        {"id": "kitchen", "dashboard": "/lovelace/0", "pages": [{"dashboard": OVERVIEW}]}
    )

    assert [page.dashboard for page in display.page_entries] == [OVERVIEW]


def test_two_pages_may_not_share_a_name() -> None:
    """A page is selected by name, so a repeated one is unreachable."""
    with pytest.raises(ValueError, match="page names must be unique"):
        DisplayConfig.model_validate(
            {
                "id": "kitchen",
                "pages": [{"dashboard": "/a/kitchen"}, {"dashboard": "/b/kitchen"}],
            }
        )


def test_an_unparseable_dwell_is_refused_at_load() -> None:
    with pytest.raises(ValueError, match="is not a duration"):
        PageConfig.model_validate({"dashboard": OVERVIEW, "dwell": "half an hour"})


@pytest.mark.parametrize(
    "dashboard,expected",
    [
        ("/lovelace-eink/kitchen", "Kitchen"),
        ("/lovelace-eink/week-ahead", "Week Ahead"),
        ("lovelace_eink/front_door", "Front Door"),
        ("/lovelace-eink/kitchen/", "Kitchen"),
        ("/lovelace/0", "0"),
        ("https://ha.example.com/lovelace-eink/tag?kiosk", "Tag"),
        ("file:///opt/maverick/mockups/wall-board.html", "Wall Board"),
        ("/", "/"),
    ],
)
def test_a_page_name_is_derived_from_the_last_segment(dashboard: str, expected: str) -> None:
    assert page_name_for(dashboard) == expected
    assert PageConfig(dashboard=dashboard).name == expected


def test_a_stored_display_keeps_only_the_page_keys_that_were_written() -> None:
    """A derived name is not written back, or it would pin what should follow
    the dashboard path (`_shorten_page`, `src/maverick/store.py`)."""
    display = DisplayConfig.model_validate(
        {
            "id": "kitchen",
            "pages": [
                {"dashboard": OVERVIEW},
                {"dashboard": CALENDAR, "name": "Week ahead", "dwell": "30m"},
            ],
            "rotate": True,
        }
    )

    written = dump_display(display)

    assert written["pages"] == [
        {"dashboard": OVERVIEW},
        {"dashboard": CALENDAR, "name": "Week ahead", "dwell": "30m"},
    ]
    assert written["rotate"] is True
    assert DisplayConfig.model_validate(written) == display


def test_a_given_name_is_kept_as_written() -> None:
    assert PageConfig(dashboard=OVERVIEW, name="What's on").name == "What's on"


# --------------------------------------------------------------------------- #
# The engine: rendering a page, and moving between them
# --------------------------------------------------------------------------- #


async def test_the_render_uses_the_page_on_the_panel(make_paged_engine) -> None:
    engine, renderer = await make_paged_engine(THREE_PAGES)

    await engine.render(DISPLAY_ID)
    engine.select_page(DISPLAY_ID, 2)
    await engine.render(DISPLAY_ID)

    assert renderer.dashboards == [OVERVIEW, WEATHER]


async def test_set_page_by_name_renders_that_page(make_paged_engine) -> None:
    engine, renderer = await make_paged_engine(THREE_PAGES)

    outcome = await engine.set_page(DISPLAY_ID, "Calendar")

    assert renderer.dashboards == [CALENDAR]
    assert engine.states[DISPLAY_ID].page_index == 1
    assert engine.current_page(DISPLAY_ID).name == "Calendar"
    # The trigger is what the render history and the MQTT state report, and
    # what tells a page change apart from a scheduled render.
    assert outcome.trigger == "page"


async def test_set_page_by_index_renders_that_page(make_paged_engine) -> None:
    engine, renderer = await make_paged_engine(THREE_PAGES)

    await engine.set_page(DISPLAY_ID, 2)

    assert renderer.dashboards == [WEATHER]
    assert engine.page_index(DISPLAY_ID) == 2


async def test_an_index_outside_the_list_is_refused(make_paged_engine) -> None:
    engine, renderer = await make_paged_engine(THREE_PAGES)

    with pytest.raises(ConfigError, match="has no page 3"):
        engine.select_page(DISPLAY_ID, 3)
    with pytest.raises(ConfigError, match="has no page -1"):
        engine.select_page(DISPLAY_ID, -1)

    assert engine.page_index(DISPLAY_ID) == 0, "a refused selection moves nothing"
    assert renderer.dashboards == []


async def test_a_name_the_display_does_not_have_is_refused(make_paged_engine) -> None:
    engine, _renderer = await make_paged_engine(THREE_PAGES)

    with pytest.raises(ConfigError, match="has no page named 'Garden'"):
        engine.select_page(DISPLAY_ID, "Garden")


async def test_next_and_previous_wrap_at_both_ends(make_paged_engine) -> None:
    engine, renderer = await make_paged_engine(THREE_PAGES)

    await engine.next_page(DISPLAY_ID)
    await engine.next_page(DISPLAY_ID)
    await engine.next_page(DISPLAY_ID)      # past the last one, back to the first
    await engine.previous_page(DISPLAY_ID)  # and back off the front

    assert renderer.dashboards == [CALENDAR, WEATHER, OVERVIEW, WEATHER]


async def test_the_page_survives_a_restart(make_paged_engine, minimal_config: Config) -> None:
    """`page_index` is persisted, or a restart would undo what an automation set."""
    engine, _renderer = await make_paged_engine(THREE_PAGES)
    engine.select_page(DISPLAY_ID, 1)

    restarted = Engine(minimal_config)
    restarted._load_state()

    assert restarted.states[DISPLAY_ID].page_index == 1
    assert restarted.states[DISPLAY_ID].page_shown_at


async def test_a_display_with_no_pages_still_renders_its_dashboard(make_paged_engine) -> None:
    engine, renderer = await make_paged_engine(None)

    await engine.render(DISPLAY_ID)

    assert renderer.dashboards == ["/lovelace/0"]
    assert engine.current_page(DISPLAY_ID).dashboard == "/lovelace/0"


# --------------------------------------------------------------------------- #
# Rotation, against a clock the test drives
# --------------------------------------------------------------------------- #


async def scheduled_tick(scheduler: RenderScheduler) -> None:
    """One fire of the display's scheduled job, without APScheduler."""
    await scheduler.trigger(DISPLAY_ID, "schedule")


async def test_rotation_advances_only_once_the_dwell_has_elapsed(make_paged_engine) -> None:
    """A dwell spans as many ticks as it takes, and the last page wraps to the first."""
    clock = FakeClock()
    engine, renderer = await make_paged_engine(
        [{"dashboard": OVERVIEW, "dwell": "10m"}, {"dashboard": CALENDAR, "dwell": "10m"}],
        rotate=True,
        clock=clock,
    )
    scheduler = RenderScheduler(engine, engine.config)

    await scheduled_tick(scheduler)   # the first page has not had its turn yet
    clock.advance(5 * 60)
    await scheduled_tick(scheduler)   # 5 of 10 minutes: stay put
    clock.advance(5 * 60)
    await scheduled_tick(scheduler)   # 10 minutes: move on
    clock.advance(5 * 60)
    await scheduled_tick(scheduler)
    clock.advance(5 * 60)
    await scheduled_tick(scheduler)   # the last page wraps to the first

    assert renderer.dashboards == [OVERVIEW, OVERVIEW, CALENDAR, CALENDAR, OVERVIEW]
    assert engine.page_index(DISPLAY_ID) == 0


async def test_a_page_without_a_dwell_changes_at_every_tick(make_paged_engine) -> None:
    clock = FakeClock()
    engine, renderer = await make_paged_engine(THREE_PAGES, rotate=True, clock=clock)
    scheduler = RenderScheduler(engine, engine.config)

    for _ in range(4):
        await scheduled_tick(scheduler)
        clock.advance(60)

    assert renderer.dashboards == [OVERVIEW, CALENDAR, WEATHER, OVERVIEW]


async def test_rotation_is_off_until_it_is_asked_for(make_paged_engine) -> None:
    clock = FakeClock()
    engine, renderer = await make_paged_engine(THREE_PAGES, clock=clock)
    scheduler = RenderScheduler(engine, engine.config)

    for _ in range(3):
        await scheduled_tick(scheduler)
        clock.advance(3600)

    assert renderer.dashboards == [OVERVIEW, OVERVIEW, OVERVIEW]


async def test_a_render_someone_asked_for_does_not_rotate(make_paged_engine) -> None:
    """Rotation belongs to the timeline. A button press shows the page that is up."""
    clock = FakeClock()
    engine, renderer = await make_paged_engine(THREE_PAGES, rotate=True, clock=clock)
    scheduler = RenderScheduler(engine, engine.config)

    await scheduled_tick(scheduler)
    clock.advance(3600)
    await scheduler.trigger(DISPLAY_ID, "button")
    await scheduler.trigger(DISPLAY_ID, "api")

    assert renderer.dashboards == [OVERVIEW, OVERVIEW, OVERVIEW]


async def test_rotation_does_nothing_for_a_display_with_one_page(make_paged_engine) -> None:
    clock = FakeClock()
    engine, renderer = await make_paged_engine(None, rotate=True, clock=clock)
    scheduler = RenderScheduler(engine, engine.config)

    await scheduled_tick(scheduler)
    clock.advance(3600)
    await scheduled_tick(scheduler)

    assert renderer.dashboards == ["/lovelace/0", "/lovelace/0"]
    assert engine.page_index(DISPLAY_ID) == 0


# --------------------------------------------------------------------------- #
# The HTTP route and the summary
# --------------------------------------------------------------------------- #


def test_the_summary_says_which_page_is_on_the_panel(paged_api) -> None:
    client, _app, _renderer = paged_api

    summary = client.get(f"/api/displays/{DISPLAY_ID}").json()

    assert summary["page"] == {
        "index": 0,
        "name": "Overview",
        "dashboard": OVERVIEW,
        "count": 3,
        "names": ["Overview", "Calendar", "Weather"],
        "rotate": False,
    }


def test_the_summary_of_a_display_without_pages_still_carries_one(
    minimal_config: Config, monkeypatch, legible_image: Image.Image
) -> None:
    paged(minimal_config, None)
    monkeypatch.setattr(
        engine_module,
        "DashboardRenderer",
        lambda ha, pool, tokens=None: RecordingRenderer(legible_image),
    )
    with TestClient(create_app(Application(minimal_config))) as client:
        summary = client.get(f"/api/displays/{DISPLAY_ID}").json()

    assert summary["page"]["count"] == 1
    assert summary["page"]["dashboard"] == "/lovelace/0"


def test_the_route_selects_a_page_by_name_index_and_step(paged_api) -> None:
    client, _app, renderer = paged_api

    by_name = client.post(f"/api/displays/{DISPLAY_ID}/page?wait=true", json={"name": "Weather"})
    assert by_name.status_code == 200
    assert by_name.json()["page"]["index"] == 2

    by_index = client.post(f"/api/displays/{DISPLAY_ID}/page?wait=true", json={"index": 1})
    assert by_index.json()["page"]["name"] == "Calendar"

    forwards = client.post(f"/api/displays/{DISPLAY_ID}/page?wait=true", json={"step": 1})
    assert forwards.json()["page"]["name"] == "Weather"

    backwards = client.post(f"/api/displays/{DISPLAY_ID}/page?wait=true", json={"step": -1})
    assert backwards.json()["page"]["name"] == "Calendar"

    assert renderer.dashboards == [WEATHER, CALENDAR, WEATHER, CALENDAR]


def test_the_route_refuses_a_page_the_display_does_not_have(paged_api) -> None:
    client, _app, renderer = paged_api

    out_of_range = client.post(f"/api/displays/{DISPLAY_ID}/page", json={"index": 9})
    unknown_name = client.post(f"/api/displays/{DISPLAY_ID}/page", json={"name": "Garden"})
    ambiguous = client.post(f"/api/displays/{DISPLAY_ID}/page", json={"index": 1, "step": 1})
    empty = client.post(f"/api/displays/{DISPLAY_ID}/page", json={})

    assert out_of_range.status_code == 422
    assert "has no page 9" in out_of_range.json()["detail"]
    assert unknown_name.status_code == 422
    assert ambiguous.status_code == 422
    assert empty.status_code == 422
    assert renderer.dashboards == [], "a refused request renders nothing"


def test_the_route_404s_for_a_display_that_does_not_exist(paged_api) -> None:
    client, _app, _renderer = paged_api

    assert client.post("/api/displays/nowhere/page", json={"index": 0}).status_code == 404


async def test_the_route_renders_in_the_background_by_default(
    minimal_config: Config, monkeypatch, legible_image: Image.Image
) -> None:
    """The page moves before the response, and the render follows it.

    A page change costs a whole render, so the default is not to hold the
    response open for it — but the summary already names the new page, which is
    what the picker repaints from.
    """
    paged(minimal_config, THREE_PAGES)
    renderer = RecordingRenderer(legible_image)
    monkeypatch.setattr(
        engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer
    )
    app = Application(minimal_config)
    api = create_app(app)

    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        await app.start()
        try:
            response = await client.post(
                f"/api/displays/{DISPLAY_ID}/page", json={"name": "Weather"}
            )
            assert response.status_code == 200
            assert response.json()["page"]["name"] == "Weather"

            for _ in range(200):
                if renderer.dashboards:
                    break
                await asyncio.sleep(0.01)
            assert renderer.dashboards == [WEATHER]
        finally:
            await app.stop()


# --------------------------------------------------------------------------- #
# MQTT: the Page select, and the commands that come back
# --------------------------------------------------------------------------- #


@pytest.fixture
async def paged_mqtt(
    minimal_config: Config,
    monkeypatch,
    legible_image: Image.Image,
    fake_clients,  # noqa: F811 - the imported fixture, requested by name
):
    """A started `Application` with pages and MQTT, over the fake paho client."""
    started: list[Application] = []

    async def _make(pages: list[dict] | None) -> tuple[Application, RecordingRenderer, FakeClient]:
        paged(minimal_config, pages)
        minimal_config.mqtt.enabled = True
        renderer = RecordingRenderer(legible_image)
        monkeypatch.setattr(
            engine_module, "DashboardRenderer", lambda ha, pool, tokens=None: renderer
        )
        app = Application(minimal_config)
        await app.start()
        started.append(app)
        return app, renderer, fake_clients[0]

    yield _make

    for app in started:
        await app.stop()


def select_payload(client: FakeClient) -> dict:
    topic = f"homeassistant/select/maverick_{DISPLAY_ID}/page/config"
    payload = client.payload_on(topic)
    assert payload, f"nothing was published to {topic}"
    return json.loads(payload)


async def test_the_page_select_offers_every_page_by_name(paged_mqtt) -> None:
    _app, _renderer, client = await paged_mqtt(THREE_PAGES)

    config = select_payload(client)

    assert config["options"] == ["Overview", "Calendar", "Weather"]
    assert config["command_topic"] == f"maverick/display/{DISPLAY_ID}/command"
    # The command topic carries words, so the chosen option arrives wrapped in
    # one `Application.handle_command` routes.
    assert config["command_template"] == "page:{{ value }}"
    assert config["state_topic"] == f"maverick/display/{DISPLAY_ID}/state"
    assert config["value_template"] == "{{ value_json.page }}"
    assert config["unique_id"] == f"maverick_{DISPLAY_ID}_page"


async def test_a_display_without_pages_gets_no_select(paged_mqtt) -> None:
    """And the topic is retracted, so a display that loses its pages loses it."""
    _app, _renderer, client = await paged_mqtt(None)

    topic = f"homeassistant/select/maverick_{DISPLAY_ID}/page/config"
    assert client.payload_on(topic) == ""
    assert (topic, "", True) in client.published


async def test_the_select_reports_the_page_on_the_state_topic(paged_mqtt) -> None:
    app, _renderer, client = await paged_mqtt(THREE_PAGES)

    await app.handle_command(DISPLAY_ID, "page:Calendar")

    state = json.loads(client.payload_on(f"maverick/display/{DISPLAY_ID}/state"))
    assert state["page"] == "Calendar"
    assert state["page_index"] == 1


async def test_the_page_commands_move_the_panel(paged_mqtt) -> None:
    app, renderer, _client = await paged_mqtt(THREE_PAGES)

    await app.handle_command(DISPLAY_ID, "page:Weather")
    await app.handle_command(DISPLAY_ID, "next_page")
    await app.handle_command(DISPLAY_ID, "previous_page")

    assert renderer.dashboards == [WEATHER, OVERVIEW, WEATHER]
    assert app.engine.page_index(DISPLAY_ID) == 2


async def test_a_command_for_a_page_that_is_gone_is_logged_not_raised(
    paged_mqtt, caplog
) -> None:
    """A retained command from before a rename must not take the service down."""
    app, renderer, _client = await paged_mqtt(THREE_PAGES)

    await app.handle_command(DISPLAY_ID, "page:Garden")

    assert renderer.dashboards == [], "an unknown page renders nothing"
    assert app.engine.page_index(DISPLAY_ID) == 0
    assert "has no page named 'Garden'" in caplog.text
