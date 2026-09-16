"""The setup UI behind Home Assistant's ingress, and when the credential is bad.

Four things this page has to get right; the first two it got wrong once, and
nothing else covers any of them.

*It has to survive a path prefix.* Ingress serves the page at
``/api/hassio_ingress/<token>/`` and proxies to the app with that prefix
stripped, telling the app nothing about it: the Supervisor sends no
``X-Ingress-Path`` (``supervisor/api/ingress.py``, ``_init_header``) and builds
its upstream URL as ``http://<ip>:<port>/<path>`` (``_create_url``). So a
root-relative ``/api/...`` in the page resolves against Home Assistant's own
origin and never reaches the app; a relative ``api/...`` resolves against the
page's base, which is the prefix under ingress and ``/`` on the published port.
The route is ``/ingress/{token}/{path:.*}``, so that base always ends in a
slash and the relative form is safe. The same now goes for ``static/...``, and
for every URL ``static/app.js`` builds, which is where the fetches went.

*It has to offer the link when the credential is broken.* A client is built
whenever a credential is configured, working or not, so asking whether one
exists would claim "connected" for a credential Home Assistant rejects — and
hide the link card exactly when it is the one thing on the page that helps.

*It has to reach the endpoints it builds.* The cards, the Add display dialog
and the per-display editor drawer are all built by ``static/app.js``, so a URL
it gets wrong fails at a click rather than at import; the sweeps below resolve
every one of them against the router.

*The page is now a shell.* The cards are drawn by ``static/app.js`` from
``GET /api/displays``, so two things have to hold that did not have to before:
the assets must actually be served — and ship in the wheel, which is a
packaging file away from the code that needs them — and the payload embedded
for the first paint must be the one the script goes on to poll.

There is no JavaScript test runner here, so what ``app.js`` *does* with those
URLs is not covered; the browser-level test is P4.3 in
``docs/implementation-plan.md``.
"""

from __future__ import annotations

import json
import re
import tomllib
from fnmatch import fnmatch
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Match

from maverick.app import Application
from maverick.config import Config
from maverick.server.api import STATIC_DIR, create_app
from maverick.server.ui import render_token_prompt

ROOT = Path(__file__).resolve().parents[1]

#: src/href/fetch targets the rendered page carries.
_EMITTED = re.compile(
    r"""(?:src|href)=['"]([^'"]+)['"]|fetch\(['"`]([^'"`]+)['"`]"""
)

#: A quoted string in the script that looks like one of this app's own URLs.
#: Template literals count: `api/displays/${id}/render` is a fetch target with
#: a hole in it, and the hole is filled in below.
_SCRIPT_TARGET = re.compile(r"""['"`](/?(?:api|static)/[^'"`\s]*)['"`]""")

#: A `${...}` in one of those, standing in for a display id.
_PLACEHOLDER = re.compile(r"\$\{[^}]*\}")

#: JavaScript comments, stripped before the scan: the script's own prose
#: explains the `/api/...` form it must not use, and quotes it to do so.
_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)

#: The first `GET /api/displays` payload, embedded so the first paint has cards.
_INITIAL = re.compile(
    r'<script type="application/json" id="initial-displays">(.*?)</script>',
    re.DOTALL,
)


async def _noop(*args, **kwargs) -> None:
    return None


@pytest.fixture
def ui_config(tmp_path) -> Config:
    return Config.model_validate(
        {
            "data_dir": str(tmp_path / "data"),
            "home_assistant": {"url": "http://homeassistant.local:8123"},
            "server": {"base_url": "http://192.168.1.10:5000"},
            "displays": [
                {"id": "kitchen", "panel": "trmnl-7in5", "transport": {"type": "file"}}
            ],
        }
    )


@pytest.fixture
def app(ui_config: Config, monkeypatch) -> Application:
    # The engine would otherwise start a browser pool and a scheduler.
    monkeypatch.setattr(Application, "start", _noop)
    monkeypatch.setattr(Application, "stop", _noop)
    return Application(ui_config)


def _page(app: Application) -> str:
    with TestClient(create_app(app)) as client:
        return client.get("/").text


def _targets(page: str) -> list[str]:
    return [a or b for a, b in _EMITTED.findall(page)]


def _script() -> str:
    """``static/app.js`` as it is served, with its comments taken out."""
    return _COMMENT.sub("", (STATIC_DIR / "app.js").read_text(encoding="utf-8"))


def _script_targets() -> list[str]:
    return _SCRIPT_TARGET.findall(_script())


# --------------------------------------------------------------------------- #
# Surviving the ingress prefix
# --------------------------------------------------------------------------- #

def test_nothing_emits_a_root_relative_app_path(app: Application) -> None:
    """``/api/...`` or ``/static/...`` here is a link that dies under ingress.

    It resolves against Home Assistant's origin, where nothing serves it, so
    the preview images break and *Link with Home Assistant* fails on a 404 it
    reports as a JSON parse error. The script is included because that is where
    the fetches live now, and the token prompt because it is the page a browser
    with no accepted token gets — and it loads the stylesheet too.
    """
    emitted = _targets(_page(app)) + _targets(render_token_prompt()) + _script_targets()
    offenders = sorted({t for t in emitted if t.startswith(("/api", "/static"))})
    assert not offenders, (
        f"{offenders} are root-relative and will miss the ingress prefix. "
        "Emit them relative to the document instead (api/..., not /api/...)."
    )


def test_the_page_still_reaches_its_own_endpoints(app: Application) -> None:
    """Relative is only correct if the targets are real app routes.

    Guards against 'fixing' the paths by deleting them: each one is resolved
    the way a browser would against the page's base and must be served.
    """
    with TestClient(create_app(app)) as client:
        targets = [
            t for t in _targets(client.get("/").text) if t.startswith(("api/", "static/"))
        ]
        assert targets, "the page emits no app endpoints at all"
        for target in targets:
            # A browser resolves `api/x` against the base `/`, giving `/api/x`.
            response = client.get(f"/{target}")
            assert response.status_code < 400, f"{target} -> {response.status_code}"


def test_the_script_only_calls_routes_this_app_serves(app: Application) -> None:
    """Every URL ``app.js`` builds has to name a route, typos included.

    Resolved against the router rather than fetched, because most of them are
    `POST` or `PUT` targets and one of them 404s honestly until something has
    rendered: what is being asserted is that the path exists, not what it
    answers.
    """
    api = create_app(app)
    targets = _script_targets()
    assert targets, "app.js calls no app endpoints at all"
    for target in targets:
        path = "/" + _PLACEHOLDER.sub("kitchen", target).split("?")[0]
        assert _resolves(api, path), (
            f"app.js builds {target}, which resolves to {path}, and no route serves it"
        )


def _resolves(api, path: str) -> bool:
    scope = {"type": "http", "method": "GET", "path": path, "root_path": "", "headers": []}
    # PARTIAL is a path that matched a route of another method — a POST target
    # reached by this GET-shaped scope — which is still a path that exists.
    return any(route.matches(scope)[0] is not Match.NONE for route in api.routes)


# --------------------------------------------------------------------------- #
# The shell and its assets
# --------------------------------------------------------------------------- #

def test_the_static_assets_are_served(app: Application) -> None:
    with TestClient(create_app(app)) as client:
        css = client.get("/static/app.css")
        js = client.get("/static/app.js")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]


def test_the_static_assets_ship_with_the_package() -> None:
    """They are in the tree but not necessarily in the wheel.

    ``StaticFiles`` checks its directory at start-up, so a package built
    without them does not serve an unstyled page — it fails to start at all.
    Nothing else here would notice, since the tests run from the source tree.
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = pyproject["tool"]["setuptools"]["package-data"]["maverick"]
    assets = sorted(p.name for p in STATIC_DIR.iterdir() if p.is_file())
    assert assets, "src/maverick/server/static is empty"
    for name in assets:
        path = f"server/static/{name}"
        assert any(fnmatch(path, pattern) for pattern in patterns), (
            f"{path} matches no [tool.setuptools.package-data] pattern in "
            f"pyproject.toml ({patterns}), so it would not ship in the wheel."
        )


def test_the_first_paint_carries_the_display_list(app: Application) -> None:
    """The embedded JSON is what the script starts from, so it has to parse.

    It is also the same payload the poll fetches; anything else and a card
    would change the moment the first poll landed.
    """
    with TestClient(create_app(app)) as client:
        page = client.get("/").text
        polled = client.get("/api/displays").json()

    block = _INITIAL.search(page)
    assert block, "the page embeds no initial display list"
    embedded = json.loads(block.group(1))
    assert [display["id"] for display in embedded] == ["kitchen"]
    assert embedded == polled


def test_the_embedded_json_cannot_close_the_script_block(app: Application) -> None:
    """A dashboard path is user text, and `</script>` in it would end the page.

    `<` cannot occur in JSON outside a string, so escaping it is enough and
    costs the parser nothing.
    """
    app.config.display("kitchen").dashboard = "/lovelace/</script><b>x"
    page = _page(app)
    block = _INITIAL.search(page)
    assert block, "the page embeds no initial display list"
    assert "</script>" not in block.group(1)
    assert json.loads(block.group(1))[0]["dashboard"] == "/lovelace/</script><b>x"


# --------------------------------------------------------------------------- #
# Offering the link when the credential does not work
# --------------------------------------------------------------------------- #

def test_a_broken_credential_still_offers_the_link(app: Application) -> None:
    """The state a user is stuck in: configured, rejected, and no way forward.

    `Engine.start` keeps the client after a failed check so the rest of the
    service can keep serving, which is why "is a client present" is the wrong
    question for this page to ask.
    """
    app.engine._ha = object()  # a client exists...
    app.engine._ha_ok = False  # ...but Home Assistant rejected it

    page = _page(app)
    assert "startLink(this)" in page, "the link card is missing for a broken credential"
    assert "Home Assistant not connected" in page


def test_a_working_credential_hides_the_link(app: Application) -> None:
    app.engine._ha = object()
    app.engine._ha_ok = True

    page = _page(app)
    assert "startLink(this)" not in page
    assert "Home Assistant connected" in page


def test_auth_status_reports_the_check_not_the_configuration(app: Application) -> None:
    """`/api/auth/status` answers the same question, so it must not differ."""
    app.engine._ha = object()
    app.engine._ha_ok = False
    with TestClient(create_app(app)) as client:
        assert client.get("/api/auth/status").json()["connected"] is False

    app.engine._ha_ok = True
    with TestClient(create_app(app)) as client:
        assert client.get("/api/auth/status").json()["connected"] is True


def test_the_base_url_blocker_says_where_to_set_it(app: Application) -> None:
    """A user reading this is in the app, not in a YAML file.

    The card names `home_assistant_token`'s option for the manual route, so the
    one blocking the button it is replacing has to do the same.
    """
    app.config.server.base_url = ""
    page = _page(app)
    assert "startLink(this)" not in page, "no base_url means nowhere to redirect back to"
    assert "Configuration tab" in page, "the blocker does not say where to set base_url"


# --------------------------------------------------------------------------- #
# Discovered tags
# --------------------------------------------------------------------------- #

def test_the_page_carries_the_discovered_tags_shell(app: Application) -> None:
    """Server-rendered and hidden: app.js fills it from
    `GET /api/ha/opendisplay/devices` and shows it only when a tag is listed."""
    page = _page(app)
    assert '<section class="discovery" id="discovery" hidden>' in page
    assert 'id="discovery-list"' in page
    assert 'id="discovery-refresh"' in page


# --------------------------------------------------------------------------- #
# The MQTT chip
# --------------------------------------------------------------------------- #

def test_mqtt_off_says_what_turning_it_on_would_give(app: Application) -> None:
    """"MQTT off" in the header was a fact nobody could act on.

    The two ways to turn it on are the Mosquitto broker app (or the
    `mqtt_host` option) under the Supervisor, and `mqtt.enabled` standalone —
    `src/maverick/ha/options.py` and `MqttConfig` respectively.
    """
    page = _page(app)
    assert "MQTT off" in page
    assert "Mosquitto broker" in page
    assert "mqtt_host" in page
    assert "mqtt.enabled: true" in page


# --------------------------------------------------------------------------- #
# The running version
# --------------------------------------------------------------------------- #

def test_the_header_names_the_running_version(app: Application) -> None:
    """Which build is serving this page, on the page.

    Twice in one afternoon a report came in about a feature that had shipped
    and was not running, and nothing on the page could settle it: the version
    was only in `GET /health` and on the Supervisor's own add-on screen. It
    comes from `maverick.app.VERSION`, which `importlib.metadata` reads off
    the installed distribution, so it describes the package actually answering
    the request rather than a number written down a second time.
    """
    from maverick.app import VERSION

    page = _page(app)
    header = page[page.index("<header>"):page.index("</header>")]
    assert f"v{VERSION}" in header, "the header does not name the running version"


def test_the_version_in_the_header_is_the_package_version() -> None:
    """And it is the same number `pyproject.toml` carries, which is what the
    add-on manifest is checked against (`tests/test_app.py`)."""
    import tomllib

    from maverick.app import VERSION

    root = Path(__file__).resolve().parent.parent
    with (root / "pyproject.toml").open("rb") as handle:
        assert tomllib.load(handle)["project"]["version"] == VERSION


# --------------------------------------------------------------------------- #
# The documentation links
# --------------------------------------------------------------------------- #

def test_the_header_links_the_written_documentation(app: Application) -> None:
    """The header used to offer `api/docs` and nothing else.

    That is the OpenAPI page — the right answer for someone writing a client,
    and no answer at all for someone asking how to build a dashboard that
    survives being printed in two inks, which is the question this project
    exists around. A user reported not being able to find any of it.
    """
    page = _page(app)
    header = page[page.index("<header>"):page.index("</header>")]
    for label in ("Design guide", "Docs", "Troubleshooting"):
        assert f">{label}</a>" in header, f"the header does not link {label}"
    # The OpenAPI page stays, it is just no longer the only thing here.
    assert 'href="api/docs"' in header


def test_every_header_doc_link_names_a_file_that_exists() -> None:
    """A link into the repository is a claim about a path.

    These are absolute GitHub URLs, so nothing offline can follow them and
    `scripts/check_links.py` — which checks relative Markdown links — never
    sees them. Renaming a page would leave the UI pointing at a 404 with
    nothing to catch it, so the paths are resolved against the working tree
    here instead.
    """
    from maverick.server import ui

    root = Path(__file__).resolve().parent.parent
    prefix = f"{ui._REPO}/blob/main/"
    for url in (ui._DOCS_URL, ui._DESIGN_GUIDE_URL, ui._TROUBLESHOOTING_URL):
        assert url.startswith(prefix), f"{url} is not a blob URL on main"
        target = root / url[len(prefix):]
        assert target.is_file(), f"the header links {url}, which is not a file in this repo"


def test_the_doc_links_open_away_from_the_page(app: Application) -> None:
    """`target=_blank` with `rel=noopener`.

    Under Home Assistant's ingress the UI is an embedded frame, so a
    same-frame navigation to GitHub would replace the setup UI inside Home
    Assistant's own chrome with no way back to it but the browser's history.
    """
    page = _page(app)
    header = page[page.index("<header>"):page.index("</header>")]
    external = re.findall(r"<a [^>]*href=\"https://[^\"]+\"[^>]*>", header)
    assert external, "no external links in the header"
    for tag in external:
        assert 'target="_blank"' in tag, f"{tag} would navigate the ingress frame away"
        assert 'rel="noopener"' in tag, f"{tag} is missing rel=noopener"


# --------------------------------------------------------------------------- #
# The Add display form
# --------------------------------------------------------------------------- #

def test_the_page_contains_the_add_display_dialog(app: Application) -> None:
    """The dialog is server-rendered shell (`_ADD_DIALOG`); app.js only fills it in."""
    page = _page(app)
    assert '<dialog id="add-dialog"' in page, "no <dialog> for adding a display"
    assert 'id="add-display-btn"' in page, "no button to open it from the header"
    for field in (
        "add-name", "add-id", "add-panel", "add-dashboard", "add-transport",
        "add-refresh", "add-quiet-hours", "add-on-change", "add-rotation", "add-submit",
    ):
        assert f'id="{field}"' in page, f"the add-display dialog is missing #{field}"
    # The geometry overrides, the crontab and the wire format left this dialog
    # for the Edit drawer's Expert settings: a dozen boxes with reference prose
    # under each was the part of it people called confusing.
    for gone in ("add-cron", "add-enabled", "add-width", "add-height", "add-color-scheme",
                 "add-dpi", "add-frame-format"):
        assert f'id="{gone}"' not in page, f"#{gone} is back in the add-display dialog"


def test_only_three_fields_sit_above_the_advanced_fold(app: Application) -> None:
    """What a person has to answer to add a display, pinned.

    The complaint this form was rebuilt for was that it asked for things a
    user has no way to decide — a transport, an interval *and* a crontab —
    before it would do anything. The panel supplies the transport
    (`DisplayConfig.transport_type`), the interval is a picker with a default,
    and everything else moved under `<details id="add-advanced">`. This fails
    if a field creeps back above it.
    """
    page = _page(app)
    dialog = page[page.index('<dialog id="add-dialog"'):page.index("</dialog>")]
    above, _, below = dialog.partition('<details class="field-group" id="add-advanced">')
    assert below, "the Advanced fold is gone from the add-display dialog"

    for field in ("add-name", "add-panel", "add-dashboard", "add-refresh"):
        assert f'id="{field}"' in above, f"#{field} should be asked for up front"
    # What the transport cannot deliver without — a tag's device id, a
    # webhook's URL — is drawn above the fold too, into this block, by
    # `onTransportChange` in static/app.js from each transport's own
    # `option_fields`; a display that fails on its first schedule for want of
    # a required option was the cost of folding it away.
    assert 'id="add-delivery"' in above, "the Delivery block should sit above the fold"
    assert 'id="add-test"' in dialog, "the dialog should offer Test delivery"
    for field in (
        "add-id", "add-transport", "add-quiet-hours", "add-on-change", "add-rotation",
    ):
        assert f'id="{field}"' in below, f"#{field} should be under the fold"


def test_nothing_under_the_fold_carries_a_browser_constraint(app: Application) -> None:
    """`required` and `pattern` inside a closed `<details>` are a trap.

    The browser refuses the submission and then cannot report why, because
    there is no focusable control to point at, so the button appears dead.
    Every check on a folded field is `submitAddDisplay`'s instead, which opens
    the fold to show the message.
    """
    page = _page(app)
    dialog = page[page.index('<dialog id="add-dialog"'):page.index("</dialog>")]
    _, _, below = dialog.partition('<details class="field-group" id="add-advanced">')
    # Comments out first: the markup explains this rule in a comment that
    # names the very attributes being searched for.
    markup = re.sub(r"<!--.*?-->", "", below, flags=re.S)
    for attribute in ("required", "pattern="):
        assert attribute not in markup, (
            f"a folded field carries {attribute!r}; validate it in submitAddDisplay instead"
        )


def test_the_refresh_picker_offers_one_interval_and_a_default(app: Application) -> None:
    """One field in minutes or hours, not `every` plus `cron` side by side.

    The values are what goes into `schedule.every`, so each has to be
    something `parse_duration` accepts (`src/maverick/config.py`), and the
    selected one has to match the interval every shipped starter config uses.
    """
    from maverick.config import parse_duration
    from maverick.server.ui import _REFRESH_CHOICES

    page = _page(app)
    assert 'value="5m" selected' in page, "the Refresh picker has no default"
    for value, label in _REFRESH_CHOICES:
        assert f'<option value="{value}"' in page, f"the Refresh picker lost {label!r}"
        if value:
            parse_duration(value)
    # The empty option is "only when something asks for it" — a display driven
    # by `on_change` or by hand, which is a real configuration
    # (`src/maverick/config.example.yaml`, the `hallway-tag` display).
    assert any(value == "" for value, _ in _REFRESH_CHOICES)


def test_the_add_display_form_only_calls_routes_this_app_serves(app: Application) -> None:
    """The four endpoints the form is built from and posts to, named explicitly.

    `test_the_script_only_calls_routes_this_app_serves` already scans the whole
    of `app.js` generically; this pins the specific targets P2.2 adds so a
    typo in one of them fails here by name rather than only in the sweep.
    """
    api = create_app(app)
    targets = _script_targets()
    for expected in (
        "api/panels", "api/transports", "api/schema/display", "api/displays",
        "api/ha/dashboards",
    ):
        assert expected in targets, f"app.js no longer references {expected}"
        assert _resolves(api, "/" + expected), f"{expected} resolves to no route"


# --------------------------------------------------------------------------- #
# The per-display editor
# --------------------------------------------------------------------------- #

def test_the_display_editor_only_calls_routes_this_app_serves(app: Application) -> None:
    """The four endpoints the drawer is opened, previewed and saved through.

    `test_the_script_only_calls_routes_this_app_serves` sweeps the whole of
    `app.js` generically; this pins the specific targets P2.3 adds, so a typo
    in one of them fails here by name. `api/displays/{id}` is three of them at
    once — the GET that fills the drawer, the PUT that saves it and the DELETE
    behind the confirmation.
    """
    api = create_app(app)
    targets = {"/" + _PLACEHOLDER.sub("kitchen", t).split("?")[0] for t in _script_targets()}
    for expected in (
        "/api/schema/display",
        "/api/displays/kitchen",
        "/api/displays/kitchen/preview.png",
        "/api/displays/kitchen/screenshot.png",
        "/api/displays/preview",
    ):
        assert expected in targets, f"app.js no longer references {expected}"
        assert _resolves(api, expected), f"{expected} resolves to no route"


def test_every_card_offers_the_editor() -> None:
    """The Edit action is on the card template, and opens the drawer.

    The cards are built by `app.js`, not by `ui.py`, so what the server ships
    is the only thing there is to check without a browser: the button exists in
    the template and is wired to the drawer rather than to nothing.
    """
    script = _script()
    assert "act-edit" in script, "no Edit action on the card template"
    assert "openEditor(id" in script, "the Edit action opens nothing"
    # The drawer draws itself from the schema rather than from a field list of
    # its own, which is the whole reason it stays in step with the models.
    assert "api/schema/display" in script


def test_the_empty_state_no_longer_tells_the_user_to_restart() -> None:
    """The empty state used to say "add a `displays:` entry ... and restart".

    That card is drawn by `app.js` now (`emptyState`), not server-rendered by
    `ui.py`, so what is checked here is what the server ships: the script must
    no longer instruct a restart, and must offer the button that replaces it.
    """
    script = _script()
    assert "restart" not in script.lower(), "app.js still tells the user to restart"
    assert "Add display" in script, "the empty state no longer offers a way to add one"
