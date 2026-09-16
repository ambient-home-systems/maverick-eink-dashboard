"""The starter dashboard: sized for the panel, and made of cards that survive.

What is worth pinning here is not the exact YAML — that will change — but the
three claims the feature makes, each of which is checkable:

1. What comes out is a dashboard Home Assistant would accept: valid YAML, a
   `views` list, a card `type` on everything in it.
2. It is sized to *this* panel. A 13.3-inch panel and a 2.9-inch shelf label
   do not get the same layout, and the numbers that separate them come from
   `LayoutBudget` rather than from a table in the generator.
3. It contains nothing the design guide says not to put on ink — no gauge, no
   filled sparkline, no camera thumbnail — however many entities are offered.
"""

from __future__ import annotations

import yaml

from maverick.config import DisplayConfig
from maverick.eink.layout import budget_for
from maverick.lovelace import CARD_ADVICE, generate_dashboard, pick_entities
from maverick.lovelace.generator import _SECTIONS

#: Every card type `CARD_ADVICE` marks as unusable on e-ink.
BANNED = {name for name, advice in CARD_ADVICE.items() if advice.startswith("NOT USED")}


def resolved(panel: str = "waveshare-7in5-mono", **overrides):
    return DisplayConfig.model_validate(
        {"id": "kitchen", "name": "Kitchen", "panel": panel, **overrides}
    ).resolved()


def cards_of(text: str) -> list[dict]:
    return yaml.safe_load(text)["views"][0]["cards"]


def state(entity_id: str, device_class: str | None = None, name: str | None = None) -> dict:
    attributes: dict[str, object] = {"friendly_name": name or entity_id}
    if device_class:
        attributes["device_class"] = device_class
    return {"entity_id": entity_id, "state": "on", "attributes": attributes}


# --------------------------------------------------------------------------- #
# It is a dashboard
# --------------------------------------------------------------------------- #

def test_the_output_is_a_dashboard_home_assistant_would_take():
    parsed = yaml.safe_load(generate_dashboard(resolved()))
    assert list(parsed) == ["views"]
    view = parsed["views"][0]
    assert view["title"] == "Kitchen"
    assert view["path"] == "eink-kitchen"
    assert view["type"] == "masonry"
    assert isinstance(view["columns"], int)
    for card in view["cards"]:
        assert "type" in card, f"a card with no type: {card}"


def test_the_first_card_is_the_heading():
    """It is what makes the panel legible across a room, and what a
    `crop_to_selector` render would be cropped to."""
    cards = cards_of(generate_dashboard(resolved()))
    assert cards[0]["type"] == "markdown"
    assert "Kitchen" in cards[0]["content"]
    # A real clock, which is why this is markdown and not a `heading` card.
    assert "now()" in cards[0]["content"]


def test_the_yaml_is_readable_after_a_round_trip():
    """The markdown card's content is multi-line, and PyYAML's default would
    make it one quoted line of `\\n` escapes — a file nobody can edit, which is
    the only thing this file is for."""
    text = generate_dashboard(resolved())
    assert "content: |" in text
    # And the entity lists are indented under their key, not at its column.
    assert "\n        entities:\n          - " in text


# --------------------------------------------------------------------------- #
# It is sized to the panel
# --------------------------------------------------------------------------- #

def test_a_bigger_panel_gets_more_columns():
    small = yaml.safe_load(generate_dashboard(resolved("opendisplay-solum-4in2-bwr")))
    large = yaml.safe_load(generate_dashboard(resolved("waveshare-13in3-gray16")))
    assert small["views"][0]["columns"] < large["views"][0]["columns"]


def test_the_column_count_is_the_layout_budgets():
    """Not a number in the generator: the same arithmetic the design guide's
    own table is printed from (`src/maverick/eink/layout.py`)."""
    display = resolved("waveshare-7in5-mono")
    budget = budget_for(display.width, display.height, display.dpi)
    view = yaml.safe_load(generate_dashboard(display))["views"][0]
    assert view["columns"] == budget.columns == 3


def test_a_rotated_panel_is_laid_out_transposed():
    """A 152×296 shelf label is rendered as 296×152 and rotated afterwards
    (`DashboardRenderer.viewport_for`), so the layout sees the landscape
    shape and must be sized for it."""
    display = resolved("opendisplay-solum-2in6-bwr")
    assert display.rotation == 270
    view = yaml.safe_load(generate_dashboard(display))["views"][0]
    transposed = budget_for(display.height, display.width, display.dpi)
    assert view["columns"] == transposed.columns


def test_a_shelf_label_gets_fewer_cards_than_a_wall_panel():
    tag = cards_of(generate_dashboard(resolved("opendisplay-solum-2in9-bwr")))
    wall = cards_of(generate_dashboard(resolved("waveshare-13in3-gray16")))
    assert len(tag) < len(wall)


def test_even_the_smallest_panel_gets_something_beyond_its_own_name():
    """A four-line panel used to come back with nothing but a heading, which
    is a panel reporting its own name and no data. Below
    `_COMPACT_BELOW_LINES` the heading shrinks and card titles are dropped so
    at least one reading fits."""
    cards = cards_of(generate_dashboard(resolved("inky-phat-bwr")))
    assert len(cards) >= 2
    assert cards[0]["type"] == "markdown"
    # No card title on a panel this small: the label would cost as much as the
    # value it labels.
    assert "title" not in cards[1]


def test_nothing_overflows_the_line_budget():
    """The budget is the point of the feature, so it is checked as arithmetic
    rather than trusted: every card's own costing, summed, fits."""
    for panel in (
        "inky-phat-bwr",
        "opendisplay-solum-2in9-bwr",
        "waveshare-7in5-mono",
        "kindle-paperwhite-3",
        "waveshare-13in3-gray16",
    ):
        display = resolved(panel)
        width, height = display.width, display.height
        if display.rotation in (90, 270):
            width, height = height, width
        budget = budget_for(width, height, display.dpi)
        picks = pick_entities(None, budget)
        assert sum(pick.lines for pick in picks) <= budget.lines, panel


# --------------------------------------------------------------------------- #
# It contains nothing that dithers badly
# --------------------------------------------------------------------------- #

def test_no_card_the_design_guide_rules_out_ever_appears():
    """Offered every entity type at once, on the biggest panel in the
    catalogue, where there is room to be tempted."""
    states = [
        state("weather.home"),
        state("sensor.kitchen_temperature", "temperature"),
        state("sensor.kitchen_humidity", "humidity"),
        state("sensor.house_power", "power"),
        state("binary_sensor.front_door", "door"),
        state("person.sam"),
        state("todo.shopping"),
        state("camera.porch"),
        state("light.hall"),
    ]
    cards = cards_of(generate_dashboard(resolved("waveshare-13in3-gray16"), states))
    types = {card["type"] for card in cards}
    assert not (types & BANNED), f"a card the design guide rules out: {types & BANNED}"


def test_a_camera_never_reaches_the_panel():
    """Camera thumbnails dither well and cost about a third of the ink budget,
    which is why they are excluded by domain rather than by card type."""
    states = [state("camera.porch"), state("sensor.kitchen_temperature", "temperature")]
    text = generate_dashboard(resolved(), states)
    assert "camera.porch" not in text


def test_entities_cards_carry_no_header_toggle():
    """A panel cannot be touched, and the toggle column costs width the value
    needs."""
    states = [state("sensor.kitchen_temperature", "temperature")]
    for card in cards_of(generate_dashboard(resolved(), states)):
        if card["type"] == "entities":
            assert card["show_header_toggle"] is False


# --------------------------------------------------------------------------- #
# Entities, real and placeholder
# --------------------------------------------------------------------------- #

def test_real_entities_are_used_when_home_assistant_answers():
    states = [
        state("sensor.kitchen_temperature", "temperature"),
        state("binary_sensor.front_door", "door"),
    ]
    text = generate_dashboard(resolved(), states)
    assert "sensor.kitchen_temperature" in text
    assert "binary_sensor.front_door" in text
    assert "PLACEHOLDER" not in text


def test_placeholders_are_used_and_declared_without_a_connection():
    """The layout is still right with no credential, and the file has to say
    the ids are not: a dashboard that silently names entities you do not have
    looks like a bug in Maverick."""
    text = generate_dashboard(resolved(), None)
    assert "PLACEHOLDER" in text
    assert "sensor.living_room_temperature" in text


def test_an_empty_home_assistant_produces_a_dashboard_with_no_entity_cards():
    """Connected, but nothing matches: that is a real answer, not a reason to
    fall back to placeholders, which would be Maverick inventing entities for
    a user who can see it is connected."""
    cards = cards_of(generate_dashboard(resolved(), []))
    assert [card["type"] for card in cards] == ["markdown"]


def test_a_long_entity_list_is_trimmed_rather_than_dropped():
    """Twenty temperature sensors should become the handful that fit, not an
    absent card."""
    states = [state(f"sensor.room_{n}_temperature", "temperature") for n in range(20)]
    cards = cards_of(generate_dashboard(resolved(), states))
    entities = [card for card in cards if card["type"] == "entities"]
    assert entities, "the temperature card was dropped instead of trimmed"
    limit = next(s["limit"] for s in _SECTIONS if s["key"] == "climate")
    assert 0 < len(entities[0]["entities"]) <= limit


def test_entities_are_ordered_by_the_name_a_person_reads():
    states = [
        state("sensor.b", "temperature", name="Attic"),
        state("sensor.a", "temperature", name="Bedroom"),
    ]
    cards = cards_of(generate_dashboard(resolved(), states))
    entities = next(card for card in cards if card["type"] == "entities")["entities"]
    assert entities == ["sensor.b", "sensor.a"], "sorted by entity id, not friendly name"


# --------------------------------------------------------------------------- #
# What the file teaches
# --------------------------------------------------------------------------- #

def test_the_file_explains_itself():
    """The generated dashboard is the documentation for the feature: it has to
    say how to install it, what the panel holds, and what was left out."""
    text = generate_dashboard(resolved())
    assert "Raw configuration" in text, "no instructions for installing it"
    assert "lines of body text" in text, "the line budget is not stated"
    assert "NOT USED" in text, "the cards that were ruled out are not named"
    assert "design-guide.md" in text, "no link to the long version"


def test_a_mono_panel_and_a_spot_ink_panel_are_advised_differently():
    mono = generate_dashboard(resolved("waveshare-7in5-mono"))
    spot = generate_dashboard(resolved("opendisplay-solum-4in2-bwr"))
    assert "two inks" in mono
    assert "accent ink" in spot


# --------------------------------------------------------------------------- #
# The route
# --------------------------------------------------------------------------- #

def test_the_route_serves_it_as_yaml(tmp_path, monkeypatch):
    """`GET /api/displays/{id}/dashboard.yaml`, behind the same token as every
    other `/api/displays/...` route."""
    from fastapi.testclient import TestClient

    from maverick.app import Application
    from maverick.config import Config
    from maverick.server.api import create_app

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(Application, "start", noop)
    monkeypatch.setattr(Application, "stop", noop)
    config = Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "server": {"api_token": "s3cret"},
            "displays": [{"id": "kitchen", "panel": "waveshare-7in5-mono"}],
        }
    )
    with TestClient(create_app(Application(config))) as client:
        assert client.get("/api/displays/kitchen/dashboard.yaml").status_code == 401

        response = client.get(
            "/api/displays/kitchen/dashboard.yaml",
            headers={"Authorization": "Bearer s3cret"},
        )
        assert response.status_code == 200
        assert yaml.safe_load(response.text)["views"][0]["path"] == "eink-kitchen"

        missing = client.get(
            "/api/displays/nope/dashboard.yaml", headers={"Authorization": "Bearer s3cret"}
        )
        assert missing.status_code == 404


def test_the_route_answers_with_placeholders_rather_than_failing(tmp_path, monkeypatch):
    """A broken Home Assistant is what a user hits *first*, and a 503 here
    would be the second thing to go wrong for them. The layout does not depend
    on the entity list, so it is served either way."""
    from fastapi.testclient import TestClient

    from maverick.app import Application
    from maverick.config import Config
    from maverick.ha.client import HomeAssistantError
    from maverick.server.api import create_app

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(Application, "start", noop)
    monkeypatch.setattr(Application, "stop", noop)
    config = Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "displays": [{"id": "kitchen", "panel": "waveshare-7in5-mono"}],
        }
    )
    application = Application(config)

    class _Failing:
        async def list_states(self):
            raise HomeAssistantError("websocket closed")

    with TestClient(create_app(application)) as client:
        # `Engine.ha` and `.ha_ok` are read-only properties over these two,
        # which is how the rest of the suite stands a client in.
        application.engine._ha = _Failing()
        application.engine._ha_ok = True
        response = client.get("/api/displays/kitchen/dashboard.yaml")
        assert response.status_code == 200
        assert "PLACEHOLDER" in response.text
