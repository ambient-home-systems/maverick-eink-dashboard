"""Build a Lovelace view sized for one panel.

The shape of the output is a whole dashboard configuration — a top-level
``views:`` list — because that is what Home Assistant's **Raw configuration
editor** takes, which is the one place a user can paste a dashboard without
building it card by card.

Three rules decide what goes in it, and each is from
[the design guide](../../../docs/design-guide.md) rather than from taste:

* **The line budget is the ceiling.** `LayoutBudget.lines`
  (`src/maverick/eink/layout.py`) is how many lines of body text the whole
  frame holds — twenty-one on a 7.5-inch panel, five on a 2.9-inch shelf label
  — so cards are added until it is spent and then stopped. A dashboard that
  overflows does not scroll; it is simply cut off.
* **Only bimodal cards.** Dark glyphs on a light ground snap to the nearest
  ink and come out crisp (design guide, section 7). Gauges, filled
  sparklines, gradients and camera thumbnails become error-diffused mush or
  spend the ink budget, so nothing here emits one and :data:`CARD_ADVICE` says
  why.
* **Meaning never rests on colour.** Most catalogued panels are two inks, and
  on a mono panel an accent is not dimmer, it is *absent*, so the layout
  carries its hierarchy in size and weight. A panel with a spot ink gets that
  ink pointed at alerts only, which is the one thing it is for.

Nothing here reaches Home Assistant. :func:`pick_entities` takes the states a
caller has already fetched (`HomeAssistantClient.list_states`) and
:func:`generate_dashboard` takes its result, so the same code path produces a
placeholder dashboard offline and a filled one when a credential works.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from ..config import ResolvedDisplay
from ..eink.layout import LayoutBudget, budget_for

#: Why each card type is here, or is not. Shown by the setup UI beside the
#: generated YAML and written into the file as its closing comment, because
#: "use this card, not that one" is the part of the design guide that changes
#: what a user builds next.
CARD_ADVICE: dict[str, str] = {
    "markdown": "Text on white. Snaps to the nearest ink, so it stays crisp.",
    "entities": "A label and a value per row. The safest card on e-ink.",
    "glance": "Several readings across one row; costs one line plus its labels.",
    "weather-forecast": "Text and line icons. Keep `show_forecast` short — each day is a column.",
    "todo-list": "Plain rows of text.",
    "gauge": "NOT USED: a tonal arc. Too thin to dither, so it snaps to a fragment of arc.",
    "sensor": "NOT USED: the graph under the value is a pale fill, the one shape that speckles.",
    "history-graph": (
        "NOT USED: filled trends dither badly. A bar chart or an unfilled line is fine."
    ),
    "picture-entity": (
        "NOT USED: a photograph dithers well but spends about a third of the ink budget."
    ),
    "light": "NOT USED: the brightness wheel is a gradient, and one bit deep after quantisation.",
}

#: Domains worth putting on a panel, in the order they are offered, with the
#: line each entity costs. A panel is read from across a room, so this favours
#: what is true *now* — a state, a temperature, who is home — over anything
#: that needs a trend to mean something.
_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "weather",
        "title": "Weather",
        "domains": ("weather",),
        "card": "weather-forecast",
        "limit": 1,
        "lines": 4,
    },
    {
        "key": "climate",
        "title": "Temperature",
        "domains": ("sensor",),
        "device_classes": ("temperature", "humidity"),
        "card": "entities",
        "limit": 6,
        "lines": 1,
    },
    {
        "key": "openings",
        "title": "Doors and windows",
        "domains": ("binary_sensor",),
        "device_classes": ("door", "window", "garage_door", "opening"),
        "card": "entities",
        "limit": 6,
        "lines": 1,
    },
    {
        "key": "people",
        "title": "Who is in",
        "domains": ("person",),
        "card": "entities",
        "limit": 6,
        "lines": 1,
    },
    {
        "key": "tasks",
        "title": "To do",
        "domains": ("todo",),
        "card": "todo-list",
        "limit": 1,
        "lines": 4,
    },
    {
        "key": "power",
        "title": "Power and battery",
        "domains": ("sensor",),
        "device_classes": ("power", "energy", "battery"),
        "card": "entities",
        "limit": 4,
        "lines": 1,
    },
)

#: What a section falls back to with no Home Assistant to ask. Real entity ids
#: are better, but a layout with named placeholders is still a layout, and it
#: is what `maverick dashboard` produces on a machine with no credential.
_PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "weather": ("weather.home",),
    "climate": ("sensor.living_room_temperature", "sensor.living_room_humidity"),
    "openings": ("binary_sensor.front_door", "binary_sensor.back_door"),
    "people": ("person.someone",),
    "tasks": ("todo.shopping_list",),
    "power": ("sensor.house_power",),
}

#: Lines the full heading card costs: the title at the `large` step, the clock
#: at the `huge` step — two lines of body on its own (design guide, section 8)
#: — and the date under it.
_HEADING_LINES = 4

#: Lines the compact heading costs: a title and a clock on one line, no date.
_COMPACT_HEADING_LINES = 2

#: Below this many body lines, the full heading would be the entire dashboard.
#: A 2.9-inch shelf label holds five lines in total, so a four-line heading
#: leaves one — less than the two a card spends on its own frame — and the
#: panel comes out showing nothing but its own name. Under this threshold the
#: heading shrinks instead, which is what makes a tag show a temperature.
_COMPACT_BELOW_LINES = 10

#: Lines a card spends on its own frame — its title and its padding — before
#: any content goes in it.
_CARD_OVERHEAD_LINES = 2

#: The same, for a card with no title. On a panel small enough to be showing
#: one thing, the card's title says what the panel already says: a tag headed
#: "Temperature" above one temperature has spent half its height on a label.
_UNTITLED_CARD_OVERHEAD_LINES = 1


@dataclass
class EntityPick:
    """One section's chosen entities, and whether they are real."""

    key: str
    title: str
    card: str
    entity_ids: list[str] = field(default_factory=list)
    #: False when these came from `_PLACEHOLDERS` rather than from a live
    #: Home Assistant, which the generated file says out loud.
    real: bool = True
    #: Body lines this card costs at the panel's own body size.
    lines: int = 0
    #: False on a panel too small to spend a line on a card heading.
    titled: bool = True


def _friendly(state: dict[str, Any]) -> str:
    attributes = state.get("attributes") or {}
    return str(attributes.get("friendly_name") or state.get("entity_id", ""))


def _matches(state: dict[str, Any], section: dict[str, Any]) -> bool:
    entity_id = str(state.get("entity_id", ""))
    domain = entity_id.split(".", 1)[0]
    if domain not in section["domains"]:
        return False
    wanted = section.get("device_classes")
    if not wanted:
        return True
    return (state.get("attributes") or {}).get("device_class") in wanted


def pick_entities(
    states: list[dict[str, Any]] | None, budget: LayoutBudget
) -> list[EntityPick]:
    """Choose what goes on the panel, stopping when the line budget runs out.

    ``states`` is what `HomeAssistantClient.list_states` returns, or ``None``
    when there is no connection — in which case every section falls back to
    the placeholders and is marked ``real=False``.

    Sections are offered in :data:`_SECTIONS` order and each is taken whole or
    not at all: half a "doors and windows" card is worse than no card, because
    the value of that card is that it is the complete list. The exception is
    the per-section ``limit``, which trims a long list *before* it is costed,
    so twenty temperature sensors become the six that fit rather than nothing.
    """
    # The heading is not optional: it is what makes the panel legible from
    # across the room, and what a cropped render is cropped to. On a panel too
    # small to afford the full one it shrinks rather than going away.
    remaining = budget.lines - heading_lines(budget)
    titled = budget.lines >= _COMPACT_BELOW_LINES
    overhead = _CARD_OVERHEAD_LINES if titled else _UNTITLED_CARD_OVERHEAD_LINES
    picks: list[EntityPick] = []

    for section in _SECTIONS:
        if states is None:
            chosen = list(_PLACEHOLDERS.get(section["key"], ()))
            real = False
        else:
            matched = [s for s in states if _matches(s, section)]
            matched.sort(key=_friendly)
            chosen = [str(s["entity_id"]) for s in matched]
            real = True
        if not chosen:
            continue
        chosen = chosen[: section["limit"]]

        cost = overhead + len(chosen) * section["lines"]
        if cost > remaining:
            # Try the card at its smallest before giving up on it: one row is
            # still a row, and a panel with four lines left can hold one.
            affordable = (remaining - overhead) // section["lines"]
            if affordable < 1:
                continue
            chosen = chosen[:affordable]
            cost = overhead + len(chosen) * section["lines"]

        remaining -= cost
        picks.append(
            EntityPick(
                key=section["key"],
                title=section["title"],
                card=section["card"],
                entity_ids=chosen,
                real=real,
                lines=cost,
                titled=titled,
            )
        )
        if remaining <= overhead:
            break

    return picks


def heading_lines(budget: LayoutBudget) -> int:
    """What the heading costs on this panel: the full one, or the compact one."""
    return (
        _COMPACT_HEADING_LINES
        if budget.lines < _COMPACT_BELOW_LINES
        else _HEADING_LINES
    )


def _heading_card(display: ResolvedDisplay, budget: LayoutBudget) -> dict[str, Any]:
    """The one card every starter dashboard gets.

    A markdown card rather than a `heading` card: markdown takes a Jinja
    template, so the time is a real clock, and the classes the e-ink
    stylesheet already styles — `.big` for the `huge` step, `.secondary` for
    the `small` one — are reachable from it (design guide, section 7, "classes
    the stylesheet already knows").

    A shelf label gets the compact form. `.big` would be 6 mm of a 25 mm panel
    and the date would be the rest of it, so on a small panel the name goes to
    the `small` step and the clock shares its line.
    """
    if budget.lines < _COMPACT_BELOW_LINES:
        return {
            "type": "markdown",
            "content": (
                f'<div class="secondary">{display.name}</div>\n'
                '<div class="big">{{ now().strftime("%H:%M") }}</div>\n'
            ),
        }
    return {
        "type": "markdown",
        "content": (
            f"# {display.name}\n"
            '<div class="big">{{ now().strftime("%H:%M") }}</div>\n'
            '<div class="secondary">{{ now().strftime("%A %-d %B") }}</div>\n'
        ),
    }


def _card_for(pick: EntityPick) -> dict[str, Any]:
    if pick.card == "weather-forecast":
        return {
            "type": "weather-forecast",
            "entity": pick.entity_ids[0],
            # Off by default: a forecast row is a column of tiny glyphs, and
            # on a panel this size the current conditions are what is legible.
            "show_forecast": False,
        }
    if pick.card == "todo-list":
        card: dict[str, Any] = {"type": "todo-list", "entity": pick.entity_ids[0]}
        if pick.titled:
            card["title"] = pick.title
        return card
    card = {"type": "entities"}
    if pick.titled:
        card["title"] = pick.title
    # No toggles: a panel is not touchable, and the switch column costs width
    # that the value needs.
    card["show_header_toggle"] = False
    card["entities"] = list(pick.entity_ids)
    return card


class _BlockDumper(yaml.SafeDumper):
    """A dumper that writes this file the way a person would have.

    Two departures from PyYAML's defaults, both so the result survives being
    edited by hand — which is the whole point of handing someone a file to
    paste and change. Multi-line strings become `|` blocks, where the markdown
    card's content would otherwise be one quoted line full of `\\n`. And
    sequences are indented under their key rather than sitting at its own
    column, which is valid either way but is not what any Home Assistant
    documentation shows.
    """

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockDumper.add_representer(str, _represent_str)


def _dump(value: Any, indent: int) -> str:
    text = yaml.dump(
        value, Dumper=_BlockDumper, sort_keys=False, default_flow_style=False, width=88
    )
    pad = " " * indent
    return "".join(f"{pad}{line}" if line.strip() else line for line in text.splitlines(True))


def _header(display: ResolvedDisplay, budget: LayoutBudget, picks: list[EntityPick]) -> list[str]:
    profile = display.profile
    placeholders = any(not pick.real for pick in picks)
    lines = [
        f"# Maverick starter dashboard for {display.name} ({profile.id}).",
        "#",
        f"# {display.width}×{display.height} at {display.dpi} dpi is "
        f"{budget.width_mm:.0f}×{budget.height_mm:.0f} mm, which holds",
        f"# {budget.columns} column(s) of about {budget.chars_per_column} characters and "
        f"{budget.lines} lines of body text",
        f"# in total. Body text is {budget.body_px}px here, and the palette is "
        f"{display.color_scheme.value}.",
        "#",
        "# Paste this into Home Assistant: Settings -> Dashboards -> Add dashboard,",
        "# then open the new dashboard, Edit, the three-dot menu, Raw configuration",
        "# editor. Point this display's `dashboard` at the view path below.",
        "#",
    ]
    if placeholders:
        lines += [
            "# The entity ids below are PLACEHOLDERS: Maverick had no Home Assistant",
            "# connection when this was generated, so it could not read your entities.",
            "# Replace them, or generate this again once the connection works and it",
            "# will fill them in for you.",
            "#",
        ]
    lines += [
        "# Every card here is one that survives quantisation. What was left out, and",
        "# why, is at the bottom of this file.",
    ]
    return lines


def _footer(display: ResolvedDisplay, budget: LayoutBudget) -> list[str]:
    lines = [
        "",
        "# ---------------------------------------------------------------------------",
        "# Cards, and why these ones",
        "#",
    ]
    for name, advice in CARD_ADVICE.items():
        lines.append(f"#   {name:<16} {advice}")
    lines += [
        "#",
        f"# This panel holds {budget.lines} lines of body text in total, headings and",
        "# blank space included. That is the ceiling: a dashboard that overflows is",
        "# cut off, not scrolled. Count in lines before you count in cards.",
        "#",
    ]
    if display.color_scheme.is_colour:
        lines += [
            "# This panel has an accent ink. Spend it on alerts and nothing else: an",
            "# accent used for decoration leaves nothing to say 'look here' with, and",
            "# the linter's `spot_ink_overuse` check will say so.",
            "#",
        ]
    else:
        lines += [
            "# This panel is two inks, so nothing here may depend on colour. Hierarchy",
            "# comes from size and weight alone, which is what the generated layout",
            "# above uses and what the e-ink stylesheet enforces.",
            "#",
        ]
    lines += [
        "# The long version, with the arithmetic behind every number above:",
        "# https://github.com/ambient-home-systems/maverick-eink-dashboard/blob/main/docs/design-guide.md",
    ]
    return lines


def dashboard_url_path(display: ResolvedDisplay) -> str:
    """The Home Assistant dashboard `url_path` the one-click create uses.

    Home Assistant requires a hyphen in a storage dashboard's `url_path`
    (`STORAGE_DASHBOARD_CREATE_FIELDS` in `homeassistant/components/lovelace/const.py`
    allows a single word only with an explicit flag), so the display id — which
    may carry none — is prefixed, and its underscores become hyphens because
    the path is a URL segment.
    """
    return f"maverick-{display.id}".replace("_", "-")


def starter_view_path(display: ResolvedDisplay) -> str:
    """The view path inside the generated dashboard, as `displays[].dashboard` takes it."""
    return f"/{dashboard_url_path(display)}/eink-{display.id}"


def generate_dashboard_config(
    display: ResolvedDisplay, states: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The starter dashboard as the mapping Home Assistant stores.

    The same cards :func:`generate_dashboard` writes as YAML, without the
    comments: this is what `lovelace/config/save` takes, so the one-click
    create in the setup UI (`POST /api/displays/{id}/dashboard/create`,
    `src/maverick/server/api.py`) sends this rather than parsing its own
    output back.
    """
    budget, picks = _plan(display, states)
    cards: list[dict[str, Any]] = [_heading_card(display, budget)]
    cards += [_card_for(pick) for pick in picks]
    return {
        "views": [
            {
                "title": display.name,
                "path": f"eink-{display.id}",
                "type": "masonry",
                "columns": budget.columns,
                "cards": cards,
            }
        ]
    }


def _plan(
    display: ResolvedDisplay, states: list[dict[str, Any]] | None
) -> tuple[LayoutBudget, list[EntityPick]]:
    """The budget and the entity picks, from the viewport rather than the panel.

    A panel with a native rotation of 90 or 270 is rendered transposed and
    rotated afterwards (`DashboardRenderer.viewport_for`,
    `src/maverick/render/dashboard.py`), so a 152×296 shelf label is laid out
    as 296×152 and gets the columns that shape holds.
    """
    width, height = display.width, display.height
    if display.rotation in (90, 270):
        width, height = height, width
    budget = budget_for(width, height, display.dpi)
    return budget, pick_entities(states, budget)


def generate_dashboard(
    display: ResolvedDisplay, states: list[dict[str, Any]] | None = None
) -> str:
    """The starter dashboard for ``display``, as YAML.

    ``states`` is every entity and its state, as
    `HomeAssistantClient.list_states` returns them; ``None`` produces the same
    layout with placeholder entity ids and a note in the file saying so.

    The viewport, not the panel, decides the layout: a panel with a native
    rotation of 90 or 270 is rendered transposed and rotated afterwards
    (`DashboardRenderer.viewport_for`, `src/maverick/render/dashboard.py`), so
    a 152×296 shelf label is laid out as 296×152 and gets the columns that
    shape holds.
    """
    budget, picks = _plan(display, states)
    config = generate_dashboard_config(display, states)
    cards = config["views"][0]["cards"]

    path = f"eink-{display.id}"
    out = _header(display, budget, picks)
    out += [
        "",
        "views:",
        f"  - title: {display.name}",
        f"    path: {path}",
        "    # `masonry` with an explicit column count, rather than a layout that",
        "    # reflows: the viewport never changes, so the number of columns is a",
        "    # decision to make once.",
        "    type: masonry",
        f"    columns: {budget.columns}",
        "    cards:",
    ]

    labels = ["the heading — size and weight are the whole hierarchy on ink"]
    labels += [f"{pick.title.lower()} — {pick.lines} of the {budget.lines} lines" for pick in picks]
    for card, label in zip(cards, labels, strict=True):
        out.append(f"      # {label}")
        out.append(_dump([card], indent=6).rstrip("\n"))

    out += _footer(display, budget)
    return "\n".join(out) + "\n"


__all__ = [
    "CARD_ADVICE",
    "EntityPick",
    "dashboard_url_path",
    "generate_dashboard",
    "generate_dashboard_config",
    "pick_entities",
    "starter_view_path",
]
