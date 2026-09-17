"""Starter dashboards: a Lovelace view built for one panel.

Maverick renders a dashboard you already have. That is the right split — it
means your panel shows the thing you designed rather than something Maverick
invented — but it left a gap at the very start, because *the dashboard you
already have is the wrong dashboard*. A phone view reflows, scrolls, uses
colour for meaning and fills cards with gauges and sparklines; a panel is a
fixed rectangle of two or four inks that cannot scroll and cannot animate, and
pointing Maverick at a phone dashboard produces a frame the linter is right to
complain about.

[The design guide](../../../docs/design-guide.md) explains all of that at
length and is worth reading, but a document is not a starting point. This
package is: it emits a Lovelace view sized to a particular panel, using the
cards that survive quantisation, filled with entities the user actually has,
with the arithmetic that decided each choice written into the file as
comments. Paste it into Home Assistant, point the display at it, and the first
render is a legible dashboard rather than a first draft to debug.

Nothing here talks to Home Assistant itself. :func:`generate_dashboard` takes
a resolved display and an optional list of entity states — the caller supplies
them, from `HomeAssistantClient.list_states` or from nowhere — and returns
YAML. With no entities it emits the same layout with placeholders and says so
in the file, so it works offline and with no credential.
"""

from .generator import (
    CARD_ADVICE,
    EntityPick,
    dashboard_url_path,
    generate_dashboard,
    generate_dashboard_config,
    pick_entities,
    starter_view_path,
)
from .rules import RULES

__all__ = [
    "CARD_ADVICE",
    "EntityPick",
    "RULES",
    "dashboard_url_path",
    "generate_dashboard",
    "generate_dashboard_config",
    "pick_entities",
    "starter_view_path",
]
