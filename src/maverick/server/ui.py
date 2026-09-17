"""The setup and monitoring UI.

Deliberately no build step and no dependencies: this runs as a Home Assistant
add-on on hardware as small as a Pi 3, on a LAN that may have no internet, and
a bundler would be more machinery than the page is worth. The stylesheet and
the script are plain files under ``static/``, served by the ``/static`` mount
in :mod:`maverick.server.api`; this module renders the shell around them.

What the page is *for* is the feedback loop. Tuning an e-ink dashboard means
re-rendering, looking at the result, adjusting a threshold and going again —
and doing that by walking to the panel is miserable. The page shows what each
panel is currently displaying, what the linter thought of it, when it will
render next, and a button to re-render now.

The cards themselves are drawn by ``static/app.js`` from ``GET /api/displays``,
which it re-polls so a scheduled render in the background shows up without a
reload. The first copy of that payload is embedded in the page, so the first
paint has content and no spinner. The adjusting half of that loop is there too:
each card's *Edit* opens a drawer that script generates from
``GET /api/schema/display``, with a Preview that renders a candidate
configuration without saving it. Two things stay server-rendered because they
have to work before any of that does: the connection chips in the header, and
:func:`_link_card`, which is the one thing on the page that helps when no
credential works — no token, no JSON, no script.

When ``server.api_token`` is set the page is behind it too, so this module
renders two things: the page itself, and :func:`render_token_prompt`, which is
what ``/`` answers with until the browser has a token to offer
(`src/maverick/server/api.py`). Both keep the token in ``sessionStorage`` —
never ``localStorage``, which would leave the secret behind for whoever opens
the browser next.
"""

from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING, Any

# `maverick.app` imports only `config` and `engine`, never the server package,
# so this is not the cycle it looks like — `api.py` imports both this module
# and `..app` the same way.
from ..app import VERSION
from ..lovelace import CARD_ADVICE, RULES

if TYPE_CHECKING:  # pragma: no cover
    from ..app import Application

# Every URL below is *document-relative* on purpose — `api/...` and
# `static/...`, never `/api/...`. Home Assistant's ingress serves this page at
# `/api/hassio_ingress/<token>/` and proxies to the app with that prefix
# stripped, telling the app nothing about it: the Supervisor sends no
# `X-Ingress-Path` (`supervisor/api/ingress.py`, `_init_header`), and its
# upstream URL is built as `http://<ip>:<port>/<path>` (`_create_url`). A
# root-relative path therefore resolves against Home Assistant's own origin
# and never reaches the app at all. A relative one resolves against the
# page's base, which is the prefix under ingress and `/` on the published
# port, so the same markup works through both.
#: Where the written documentation lives. Absolute, and the only absolute URLs
#: on the page — everything else is document-relative so it survives Home
#: Assistant's ingress prefix (see the note above). These cannot be relative:
#: the docs are not served by this app, they are files in the repository, and a
#: panel on a LAN with no internet simply gets a link that does not open, which
#: is better than no link at all. `api/docs` below is different — that one is
#: this app's own OpenAPI page and stays relative.
_REPO = "https://github.com/ambient-home-systems/maverick-eink-dashboard"
_DOCS_URL = f"{_REPO}/blob/main/docs/README.md"
_DESIGN_GUIDE_URL = f"{_REPO}/blob/main/docs/design-guide.md"
_TROUBLESHOOTING_URL = f"{_REPO}/blob/main/docs/troubleshooting.md"

# The header's documentation links. Until now the only thing here was
# `api/docs`, which is the machine-readable route reference — useful to
# somebody writing a client, and no help at all to somebody asking how to build
# a dashboard that survives being printed in two inks. That question has a
# 1,100-line answer in the repository that the tool never pointed at, so it is
# named here, first.
_DOC_LINKS = f"""<span class="sub spacer doclinks">
    <a href="#rules" class="rules-open"
       title="The five rules for a dashboard on ink, on one screen">Designing for ink</a>
    <a href="{_DOCS_URL}" target="_blank" rel="noopener"
       title="Every page: guides, recipes per device, and the generated reference">Docs</a>
    <a href="{_TROUBLESHOOTING_URL}" target="_blank" rel="noopener"
       title="The failures people actually hit, keyed to the message Maverick prints"
       >Troubleshooting</a>
    <a href="api/docs" title="This server's own OpenAPI reference">API</a>
  </span>"""

_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Maverick</title>
<link rel="stylesheet" href="static/app.css"></head>"""

# The form submits to the page itself with `token` as its one field, so it
# still works if the script never loaded: `?token=` is what the server accepts
# from a navigation anyway. With the script, `applyToken` intercepts it and
# stores the token for the tab first.
_TOKEN_FORM = """<form class="tokenbox" id="token-box" hidden method="get">
    <label for="token-field">API token</label>
    <input id="token-field" name="token" type="password" autocomplete="off"
           spellcheck="false">
    <button type="submit">Use</button>
  </form>"""

_MQTT_HELP = """<details class="chip">
    <summary>MQTT off</summary>
    <div class="chip-body">
      <p>With a broker, every display becomes a Home Assistant device: a
      <code>button</code> to render it from an automation, a
      <code>switch</code> to pause its schedule, an <code>image</code> showing
      what the panel is showing, and sensors for the last render, the status
      and how long it took. Without one, none of those entities exist.</p>
      <p><b>In the app:</b> install the <b>Mosquitto broker</b> app and
      Maverick picks it up on its next start, or fill in the
      <code>mqtt_host</code> option on the Configuration tab to use a broker
      of your own.</p>
      <p><b>Standalone:</b> set <code>mqtt.enabled: true</code> in your config
      file, with <code>mqtt.host</code> if the broker is not
      <code>core-mosquitto</code>.</p>
    </div>
  </details>"""

# The Add display dialog. Static markup for a fixed set of fields, and the one
# place in the UI that decides how much a person has to know before a panel
# works.
#
# What is above the Advanced fold is what a display genuinely cannot be
# guessed: a name, which panel it is, and which dashboard to put on it. Every
# other field here has a default that is right far more often than not, so it
# lives under `<details>` rather than in front of someone adding their first
# display:
#
# * **Transport** defaults to the panel's own `default_transport`
#   (`src/maverick/devices/panels.yaml`), which is the whole point of naming a
#   panel — a BLE shelf label is reached over BLE and a Waveshare module over
#   HTTP, and neither is a decision the user has information to make. The
#   picker's first option is that, spelled out, so it is visible without being
#   a question.
# * **Refresh** is one `<select>` of intervals, not the `every`/`cron` pair the
#   model takes. Those two are mutually exclusive
#   (`ScheduleConfig._exclusive`, `src/maverick/config.py`), so offering both
#   as empty text boxes asked the user to know a crontab and to know which
#   field wins. Cron is still there, under Advanced, described as what it is:
#   the override for a schedule an interval cannot express.
# * **Id** is derived from the name by `slugify` in `static/app.js` and only
#   needs touching when the derived one is not wanted.
# * **The geometry overrides, the crontab and the wire format are not here at
#   all.** They were, under Advanced, and a dozen boxes with reference prose
#   under each was the part of this dialog people called confusing. They live
#   on the Edit drawer under *Expert settings* now; the fold here is four
#   things a first day might need.
#
# This form's shape does not change, so it is server-rendered like the rest of
# the shell and app.js only fills in what has to come from `/api/panels`,
# `/api/transports` and `/api/schema/display`: panel and transport options, and
# every `data-help` node's text, which is each field's own
# `Field(description=...)` in `src/maverick/config.py` so the two copies cannot
# drift apart. Working with no script is not a goal here, unlike the rest of
# the page: there is nothing for the dialog to do without one.

#: The Refresh picker's options: label -> what goes in `schedule.every`.
#:
#: `5m` is first and selected because it is what every shipped starter config
#: uses (`src/maverick/config.example.yaml`, `app/rootfs/usr/share/maverick/
#: maverick.yaml`), so the UI and the YAML a user may already have agree. The
#: long end of the range matters more than it looks: an e-ink refresh is
#: visible and, on a battery panel, expensive, so "once a day" is a real
#: answer for a panel showing a calendar rather than a joke option.
_REFRESH_CHOICES: tuple[tuple[str, str], ...] = (
    ("5m", "Every 5 minutes"),
    ("10m", "Every 10 minutes"),
    ("15m", "Every 15 minutes"),
    ("30m", "Every 30 minutes"),
    ("1h", "Every hour"),
    ("2h", "Every 2 hours"),
    ("6h", "Every 6 hours"),
    ("12h", "Every 12 hours"),
    ("24h", "Once a day"),
    ("", "Only when something asks for it"),
)

_REFRESH_OPTIONS = "\n          ".join(
    f'<option value="{value}"{" selected" if value == "5m" else ""}>{label}</option>'
    for value, label in _REFRESH_CHOICES
)

_ADD_DIALOG = f"""<dialog id="add-dialog" aria-labelledby="add-dialog-title">
  <form id="add-form">
    <h2 id="add-dialog-title">Add display</h2>
    <p class="dialog-error" id="add-dialog-error" role="alert" hidden></p>

    <div class="field">
      <label for="add-name">Name</label>
      <input id="add-name" name="name" type="text" autocomplete="off" autofocus
             placeholder="Kitchen">
      <div class="help" data-help="name"></div>
    </div>

    <div class="field">
      <label for="add-panel">Panel</label>
      <select id="add-panel" name="panel" required></select>
      <div class="help" id="add-panel-notes" hidden></div>
      <!-- What this panel settles on its own, so the fold below reads as
           somewhere to disagree rather than somewhere to go and finish. -->
      <div class="help" id="add-panel-summary"></div>
      <div class="field-error" data-error="panel"></div>
    </div>

    <div class="field">
      <label for="add-dashboard">Dashboard</label>
      <input id="add-dashboard" name="dashboard" type="text" value="/lovelace/0"
             autocomplete="off" list="add-dashboard-list">
      <!-- Populated from GET /api/ha/dashboards when the dialog opens
           (src/maverick/server/static/app.js); free text stays valid, since an
           absolute URL or a file:// page is a valid dashboard too
           (`resolve_url`, src/maverick/render/dashboard.py). -->
      <datalist id="add-dashboard-list"></datalist>
      <div class="help" data-help="dashboard"></div>
      <!-- The one prompt in the flow that says a dashboard for ink is its own
           design problem. It points at the generated starter, which is the
           part that works with no internet. -->
      <div class="help">No dashboard for e-ink yet? Add the display, then use
        <b>Dashboard starter</b> on its card.</div>
      <div class="field-error" data-error="dashboard"></div>
    </div>

    <div class="field">
      <label for="add-refresh">Refresh</label>
      <select id="add-refresh" name="refresh">
          {_REFRESH_OPTIONS}
      </select>
      <div class="help" id="add-refresh-help">Every refresh is visible on the
        panel, and costs a battery one wake.</div>
      <div class="field-error" data-error="schedule"></div>
    </div>

    <!-- What the chosen transport cannot do without: the tag to deliver to for
         an OpenDisplay panel, the endpoint for a webhook. Drawn by app.js from
         each transport's `option_fields` (src/maverick/transports/base.py) —
         only the required options and the mode that decides them; everything
         optional stays under the fold. Hidden for a transport that needs
         nothing, which is most of them. -->
    <div class="field" id="add-delivery" hidden>
      <label id="add-delivery-label">Delivery</label>
      <div class="help" id="add-delivery-help"></div>
      <div id="add-delivery-options"></div>
      <div class="field-error" data-error="delivery"></div>
    </div>

    <!-- Four things a person might reasonably want on day one that the
         defaults do not settle: which way the panel is mounted, when not to
         refresh, what should trigger a refresh, and a different way to reach
         the panel. Everything else the model takes — geometry overrides, a
         crontab, the wire format — is on the display's Edit drawer under
         Expert settings, where it is one click away rather than in the way. -->
    <details class="field-group" id="add-advanced">
      <summary>More settings</summary>

      <div class="field">
        <label for="add-rotation">Rotation</label>
        <select id="add-rotation" name="rotation">
          <option value="">panel default</option>
          <option value="0">0&deg;</option>
          <option value="90">90&deg;</option>
          <option value="180">180&deg;</option>
          <option value="270">270&deg;</option>
        </select>
        <div class="help" data-help="rotation"></div>
        <div class="field-error" data-error="rotation"></div>
      </div>
      <div class="field">
        <label for="add-quiet-hours">Quiet hours</label>
        <input id="add-quiet-hours" name="quiet_hours" type="text"
               placeholder="23:00-06:30" autocomplete="off">
        <div class="help" data-help="schedule.quiet_hours"></div>
      </div>
      <div class="field">
        <label for="add-on-change">Refresh when these change</label>
        <input id="add-on-change" name="on_change" type="text"
               placeholder="sensor.a, sensor.b" autocomplete="off">
        <div class="help" data-help="schedule.on_change"></div>
      </div>

      <div class="field">
        <label for="add-transport">Delivery method</label>
        <select id="add-transport" name="transport"></select>
        <div class="help" id="add-transport-help"></div>
      </div>
      <div id="add-transport-options"></div>
      <div class="field-error" data-error="transport"></div>

      <div class="field">
        <label for="add-id">Id</label>
        <!-- No `required` and no `pattern`: a constraint violation on a
             control inside a closed `<details>` cannot be reported, because
             the browser has nothing focusable to point at, and the submission
             is blocked with nothing shown. `submitAddDisplay` checks the same
             two rules in script and opens the fold to say so. -->
        <input id="add-id" name="id" type="text" autocomplete="off">
        <!-- The rule DisplayConfig._slug enforces (src/maverick/config.py): -->
        <div class="help">Made from the name. Lower-case letters, digits,
          <code>-</code> or <code>_</code>. It cannot change once saved.</div>
        <div class="field-error" data-error="id"></div>
      </div>
    </details>

    <p class="dialog-error" id="add-network-error" role="alert" hidden></p>

    <!-- Asks the transport whether a delivery would arrive, before anything
         is saved: `POST /api/displays/probe` (src/maverick/server/api.py). -->
    <div class="probe-row">
      <button type="button" id="add-test">Test delivery</button>
      <span class="probe-result" id="add-test-result" role="status"></span>
    </div>

    <div class="dialog-actions">
      <button type="button" id="add-cancel">Cancel</button>
      <button type="submit" id="add-submit">Add display</button>
    </div>
  </form>
</dialog>"""

def _rules_dialog() -> str:
    """The five rules and the card list, as one screen.

    Rendered from `RULES` (`src/maverick/lovelace/rules.py`) and `CARD_ADVICE`
    (`src/maverick/lovelace/generator.py`) rather than written here, so the
    dialog, the starter's comments and the design guide's own summary cannot
    say three different things. The long guide is linked at the bottom for
    the mechanism behind each rule.
    """
    rules = "\n".join(
        f"<li><b>{html.escape(title)}</b><span>{html.escape(what)}</span>"
        f"<span class=\"why\">{html.escape(why)}</span></li>"
        for title, what, why in RULES
    )
    good = [
        (name, advice) for name, advice in CARD_ADVICE.items() if not advice.startswith("NOT USED")
    ]
    bad = [
        (name, advice.removeprefix("NOT USED: "))
        for name, advice in CARD_ADVICE.items()
        if advice.startswith("NOT USED")
    ]
    cards = lambda rows: "\n".join(  # noqa: E731 - a two-line template helper
        f"<li><code>{html.escape(name)}</code> {html.escape(advice)}</li>" for name, advice in rows
    )
    return f"""<dialog id="rules" aria-labelledby="rules-title">
  <div class="rules-head">
    <h2 id="rules-title">Designing for ink</h2>
    <button type="button" class="drawer-close rules-close" aria-label="Close">&times;</button>
  </div>
  <div class="rules-body">
    <p class="meta">Five rules cover almost everything. The starter dashboard follows
      them for you; the checks under each card say which one a page broke.</p>
    <ol class="rules">
{rules}
    </ol>
    <div class="rules-cards">
      <div><h3>Cards that work</h3><ul>{cards(good)}</ul></div>
      <div><h3>Cards to avoid</h3><ul>{cards(bad)}</ul></div>
    </div>
    <p class="meta">The long version, with the numbers behind every rule:
      <a href="{_DESIGN_GUIDE_URL}" target="_blank" rel="noopener">the design guide</a>.</p>
  </div>
</dialog>"""


# The tags Home Assistant's OpenDisplay integration has already found, offered
# as displays to add. Server-rendered shell, filled by app.js from
# `GET /api/ha/opendisplay/devices`; hidden until that answers with a tag, so a
# page with none — or no connection — looks exactly as it did.
_DISCOVERY = """<section class="discovery" id="discovery" hidden>
  <div class="discovery-head">
    <h2>Tags Home Assistant can see</h2>
    <span class="sub">Found by the OpenDisplay integration. Add one and the
      panel, the transport and the device id are filled in for you.</span>
    <button type="button" class="discovery-refresh" id="discovery-refresh">Refresh</button>
  </div>
  <div class="discovery-list" id="discovery-list"></div>
</section>"""


def render_ui(application: Application, displays: list[dict[str, Any]]) -> str:
    """The page shell, with ``displays`` embedded for the first paint.

    ``displays`` is exactly what ``GET /api/displays`` returns — the caller
    passes it in rather than this module building it, so the JSON the page
    starts from and the JSON it polls cannot describe a display differently
    (`src/maverick/server/api.py`).
    """
    config = application.config

    # `engine.ha` is "a credential is configured", not "it works": the client is
    # built whenever one is present and kept even when the check fails
    # (`src/maverick/engine.py`, `Engine.start`). Asking it here would claim
    # "connected" for a credential Home Assistant rejects, and hide the link
    # card below exactly when a broken credential makes it the one thing on
    # this page the user needs.
    ha_connected = application.engine.ha_ok
    mqtt_connected = bool(application.engine.mqtt and application.engine.mqtt.connected)

    ha_chip = (
        '<span class="chip ok">Home Assistant connected</span>'
        if ha_connected
        else '<span class="chip err">Home Assistant not connected</span>'
    )
    # Connected, the chip is a fact and says so in one line. Off, it is the
    # one header state a user can act on, so it opens what MQTT would give
    # them and how to turn it on.
    mqtt_chip = (
        '<span class="chip ok">MQTT connected</span>' if mqtt_connected else _MQTT_HELP
    )

    # The link panel goes above the grid: with no credential nothing else on
    # the page can work, and "not connected" in the header is not an
    # instruction.
    lede = "" if ha_connected else f'<div class="lede">{_link_card(application)}</div>'

    count = len(config.displays)
    # The version, in the header, because twice in one afternoon a report came
    # in about a feature that *was* shipped and *was not* running — and there
    # was no way to tell which build the page came from without reaching
    # `/health` or the Supervisor's add-on page. `VERSION` is read from the
    # installed distribution (`src/maverick/app.py`), so this is the package
    # actually serving the request rather than a number written down twice.
    version = html.escape(VERSION)
    # `</script>` inside the JSON would end the block early; `<` cannot occur
    # in JSON outside a string, and `\\u003c` is the same string to any parser.
    embedded = json.dumps(displays).replace("<", "\\u003c")

    return f"""{_HEAD}
<body>
<header>
  <h1>Maverick</h1>
  <span class="sub version" title="Maverick {version} — the package serving this page">
    v{version}</span>
  <span class="sub">{count} display{"" if count == 1 else "s"}</span>
  <button type="button" id="add-display-btn" class="add-btn"
          aria-haspopup="dialog">Add display</button>
  {ha_chip}
  {mqtt_chip}
  {_DOC_LINKS}
  <!-- Hidden until a fetch comes back 401. With no `server.api_token` set
       that never happens and the page looks exactly as it did before. -->
  {_TOKEN_FORM}
</header>
<p class="notice" id="notice" role="status" hidden></p>
{lede}
{_DISCOVERY}
<main id="displays"></main>
{_ADD_DIALOG}
{_rules_dialog()}
<script type="application/json" id="initial-displays">{embedded}</script>
<script type="module" src="static/app.js"></script>
</body></html>"""


def _link_card(application: Application) -> str:
    """The 'Link with Home Assistant' panel, shown until a credential works.

    Maverick needs a credential that authenticates a *browser session*, not
    just the REST API, because it renders the real dashboard rather than
    redrawing it from entity states — which is why the supervisor token an app
    gets for free is not enough. The IndieAuth flow gets one without the user
    copying a secret by hand; the manual route stays documented underneath for
    anyone whose `base_url` cannot be reached from their browser.
    """
    from ..ha import auth as ha_auth

    ha = application.config.home_assistant
    base_url = application.config.server.base_url

    blocker = ""
    if not base_url:
        blocker = (
            "Set <code>base_url</code> first — Home Assistant has to redirect "
            "back to Maverick, and that is the address it will use. In the app "
            "it is the <code>base_url</code> option on the Configuration tab; "
            "give it the address panels reach Maverick on, for example "
            "<code>http://192.168.1.10:5000</code>."
        )
    else:
        try:
            ha_auth.client_id_for(base_url)
        except ha_auth.AuthError:
            blocker = (
                f"<code>base_url</code> is {html.escape(base_url)}, which is not "
                "an http(s) URL Home Assistant can redirect back to."
            )

    if blocker:
        action = f"<div class='meta warn'>{blocker}</div>"
    else:
        action = (
            "<div class='row'>"
            "<button onclick='startLink(this)'>Link with Home Assistant</button>"
            "</div>"
            "<div class='meta'>Opens Home Assistant, asks you to log in once, "
            "and comes back. Nothing to copy.</div>"
        )

    return f"""
<section class="card">
  <h2>Home Assistant <span class="pill err">not connected</span></h2>
  <div class="meta">
    Maverick renders your real dashboard in a browser, so it needs a login
    session for <code>{html.escape(ha.url)}</code> — not just API access. The
    app's own supervisor token cannot provide one.
  </div>
  {action}
  <div class="meta">
    Prefer to do it by hand? Create a long-lived access token under your Home
    Assistant profile &rarr; Security, and set <code>home_assistant.token</code>
    (the <code>home_assistant_token</code> option in the app).
  </div>
</section>"""


def render_token_prompt(supplied: bool = False) -> str:
    """What ``/`` answers with while the browser has no token it can offer.

    The UI is behind ``server.api_token`` like everything else it shows
    (`src/maverick/server/api.py`), but a browser navigating to a page cannot
    send an ``Authorization`` header, so the dependency's JSON ``detail`` would
    be a dead end for the one audience that reads it. This asks for the token
    and re-opens the page as ``?token=...``, which a navigation *can* carry.

    ``supplied`` says whether the rejected request already carried a token.
    When it did, the stored one is wrong: say so, and drop it — offering it
    again is what would turn the redirect below into a loop.

    The stylesheet and script come from ``/static``, which is *not* behind the
    token: this page is served precisely when the browser has no valid one, and
    it would otherwise arrive unstyled and unable to store what it is given.
    """
    note = (
        "That token was not accepted. Check <code>server.api_token</code> in "
        "Maverick's configuration — in the app it is the "
        "<code>api_token</code> option on the Configuration tab."
        if supplied
        else "This Maverick is protected by <code>server.api_token</code>. "
        "Enter it to open the setup UI; it is kept for this tab only."
    )
    return f"""{_HEAD}
<body>
<div class="prompt">
  <h1>Maverick</h1>
  <p class="{"err" if supplied else ""}">{note}</p>
  <form class="tokenbox" method="get">
    <label for="token-field">API token</label>
    <input id="token-field" name="token" type="password" autocomplete="off"
           spellcheck="false" autofocus>
    <button type="submit">Open</button>
  </form>
  <p>Opening Maverick from inside Home Assistant never asks for this: those
  requests arrive through the app's ingress, which Home Assistant has already
  put a login in front of.</p>
</div>
<script type="module">
import {{ runTokenPrompt }} from './static/app.js';
const REJECTED={json.dumps(supplied)};
runTokenPrompt(REJECTED);
</script>
</body></html>"""


__all__ = ["render_token_prompt", "render_ui"]
