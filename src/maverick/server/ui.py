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
paint has content and no spinner. Two things stay server-rendered because they
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
    # `</script>` inside the JSON would end the block early; `<` cannot occur
    # in JSON outside a string, and `\\u003c` is the same string to any parser.
    embedded = json.dumps(displays).replace("<", "\\u003c")

    return f"""{_HEAD}
<body>
<header>
  <h1>Maverick</h1>
  <span class="sub">{count} display{"" if count == 1 else "s"}</span>
  {ha_chip}
  {mqtt_chip}
  <span class="sub spacer"><a href="api/docs">API docs</a></span>
  <!-- Hidden until a fetch comes back 401. With no `server.api_token` set
       that never happens and the page looks exactly as it did before. -->
  {_TOKEN_FORM}
</header>
<p class="notice" id="notice" role="status" hidden></p>
{lede}
<main id="displays"></main>
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
