"""HTTP API and setup UI.

Serves three audiences:

* **Panels** fetch frames from ``/api/displays/{id}/frame``. That endpoint is
  built for battery devices: strong ETags so an unchanged frame costs a 304 and
  no e-ink refresh, a ``Date`` header so firmware needs no SNTP, and an
  ``X-Maverick-Next-Refresh`` hint so a device knows how long to deep-sleep.
* **Home Assistant** posts to ``/api/displays/{id}/render`` — the escape hatch
  for anyone not using MQTT discovery, via ``rest_command``.
* **People** get a setup UI at ``/`` to see what each panel is showing, check
  lint findings, and copy a generated ESPHome config.

``server.api_token`` gates all three when it is set — including the UI and the
preview PNGs, which anyone on the LAN could otherwise read off the published
port. The one way past it is Home Assistant's ingress proxy, recognised by its
peer address in :mod:`maverick.ha.supervisor`: Home Assistant has already
authenticated whoever is on the other end of that connection, and it has no
token of ours to send.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import logging
from collections.abc import Awaitable
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator

from ..app import VERSION, Application
from ..config import DisplayConfig
from ..devices import all_panels
from ..devices.guess import guess_panel
from ..engine import HISTORY_LIMIT
from ..esphome import describe_esphome, esphome_applicable
from ..esphome import install as esphome_install
from ..ha import auth as ha_auth
from ..ha import client as ha_client
from ..ha import supervisor
from ..store import dump_display
from ..transports import available_transports
from .copy import lint_advice, ui_copy
from .ui import render_token_prompt, render_ui

log = logging.getLogger(__name__)

#: The setup UI's stylesheet and script, served at `/static`. They ship with
#: the package (`[tool.setuptools.package-data]` in `pyproject.toml`);
#: `StaticFiles` checks the directory when `create_app` builds the mount, so a
#: package assembled without them fails loudly rather than serving a page with
#: no styling and no script.
STATIC_DIR = Path(__file__).parent / "static"


class ScheduleToggle(BaseModel):
    """Body of `POST /api/displays/{id}/schedule`."""

    enabled: bool


class PageSelect(BaseModel):
    """Body of `POST /api/displays/{id}/page`: which page to put on the panel.

    Three ways to say it, exactly one per request. `index` and `name` are
    absolute and `step` is relative — `1` for the next page and `-1` for the
    previous, wrapping at either end. They are separate fields rather than one
    polymorphic value because an index and a name are not interchangeable: a
    page may be called "2" (`Engine.select_page`, `src/maverick/engine.py`).
    """

    index: int | None = None
    name: str | None = None
    step: int | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> PageSelect:
        given = [
            key for key in ("index", "name", "step") if getattr(self, key) is not None
        ]
        if len(given) != 1:
            raise ValueError(
                "send exactly one of 'index', 'name' or 'step'"
                + (f", not {', '.join(given)}" if given else "")
            )
        return self


class DashboardCreate(BaseModel):
    """Body of `POST /api/displays/{id}/dashboard/create`."""

    #: Replace the contents of a dashboard that already exists at the same
    #: `url_path`. Off, such a dashboard is the user's and the route answers
    #: 409 instead.
    overwrite: bool = False


class EsphomeInstall(BaseModel):
    """Body of `POST /api/displays/{id}/esphome/install`."""

    destination: str
    #: Replace a file that exists there with different content. Off, such a
    #: file is the user's and the route answers 409 instead.
    overwrite: bool = False


#: TRMNL firmware carries its API key in its own header rather than in
#: ``Authorization``. It sends ``Access-Token``; the BYOS reference docs spell
#: the same field ``ACCESS_TOKEN``, and HTTP header names are case-insensitive
#: but underscores and hyphens are not interchangeable, so both are accepted.
_TOKEN_HEADERS = ("access-token", "access_token")


def _authenticated(app: Application, request: Request) -> bool:
    """Whether this request satisfies ``server.api_token``.

    True when no token is configured — the default, which leaves every route
    open — when the request carries the right one, or when it arrived through
    Home Assistant's ingress proxy.

    The token may be presented three ways: an ``Authorization: Bearer`` header,
    one of the TRMNL ``Access-Token`` headers, or a ``?token=`` query
    parameter. All three carry the same secret and are compared the same way —
    the extra header names widen how a client may present the token, not who is
    let in. The query parameter is what a browser's ``<img>`` and plain anchors
    use, since neither can send a header.
    """
    expected = app.config.server.api_token
    if not expected:
        return True
    # Ingress puts Home Assistant's own login in front of us and sends no token
    # of ours, so the proxy's peer address is the gate for that path. It is a
    # peer address and not a header because the published port takes requests
    # from the whole LAN; see `maverick.ha.supervisor`.
    client = request.client
    if supervisor.request_is_from_ingress(client.host if client else None):
        return True
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else ""
    if not supplied:
        for name in _TOKEN_HEADERS:
            supplied = request.headers.get(name, "")
            if supplied:
                break
    if not supplied:
        supplied = request.query_params.get("token", "")
    # Constant-time compare: this token gates re-rendering and frame access.
    return hmac.compare_digest(supplied, expected)


def _require_token(app: Application):
    """Token dependency, active only when server.api_token is set."""

    async def dependency(request: Request) -> None:
        if not _authenticated(app, request):
            raise HTTPException(status_code=401, detail="invalid or missing API token")

    return dependency


def create_app(application: Application) -> FastAPI:
    api = FastAPI(
        title="Maverick",
        version=VERSION,
        description="Render Home Assistant dashboards to e-ink displays.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    auth = Depends(_require_token(application))

    @api.on_event("startup")
    async def _startup() -> None:  # pragma: no cover - lifecycle
        await application.start()

    @api.on_event("shutdown")
    async def _shutdown() -> None:  # pragma: no cover - lifecycle
        await application.stop()

    # ------------------------------------------------------------- status --

    @api.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": VERSION,
            "displays": len(application.config.enabled_displays),
            "home_assistant": application.engine.ha is not None,
            "mqtt": application.engine.mqtt is not None
            and application.engine.mqtt.connected,
        }

    @api.get("/api/panels")
    async def panels() -> list[dict[str, Any]]:
        """The panel catalogue, including the two fields the Add display form
        needs to placehold `DisplayConfig.rotation` and `.frame_format`:
        `rotation` is `PanelProfile.native_rotation` and `frame_format` is
        `PanelProfile.default_format` (`src/maverick/devices/profiles.py`) —
        neither was exposed here before the setup UI had a form that resolved
        a panel's own value for those two overrides.
        """
        return [
            {
                "id": p.id, "name": p.name, "vendor": p.vendor,
                "width": p.width, "height": p.height,
                "color_scheme": p.color_scheme.value, "dpi": p.dpi,
                "rotation": p.native_rotation,
                "frame_format": p.default_format,
                "supports_partial": p.supports_partial,
                "default_transport": p.default_transport,
                "esphome_model": p.esphome_model,
                "notes": p.notes,
            }
            for p in all_panels()
        ]

    @api.get("/api/transports")
    async def transports() -> list[dict[str, Any]]:
        return [
            {"name": name, "pushes": cls.pushes, "description": cls.description}
            for name, cls in sorted(available_transports().items())
        ]

    @api.get("/api/ha/dashboards", dependencies=[auth])
    async def ha_dashboards() -> list[dict[str, Any]]:
        """Every Lovelace dashboard and its views, for the Dashboard field's picker.

        `HomeAssistantClient.list_dashboards` (`src/maverick/ha/client.py`) is
        what actually asks Home Assistant; this just gates it behind a working
        connection, since a WebSocket call against no client or a broken one
        is not something a picker should surface as a 500.
        """
        if not application.engine.ha_ok or application.engine.ha is None:
            raise HTTPException(
                status_code=503,
                detail="Not connected to Home Assistant, so its dashboards cannot be listed.",
            )
        try:
            return await application.engine.ha.list_dashboards()
        except ha_client.HomeAssistantError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    # ----------------------------------------------------------- displays --

    @api.get("/api/schema/display", dependencies=[auth])
    async def display_schema() -> dict[str, Any]:
        """What a form needs to draw itself: the display model plus transport options.

        `DisplayConfig.model_json_schema()` carries every field's own
        `Field(description=...)` as help text. `TransportConfig` is the one
        model that allows extra keys (`extra="allow"`,
        `src/maverick/config.py`), so its per-transport options are not in the
        schema at all; each transport's own `options_doc`
        (`src/maverick/transports/base.py`) is the only description of them
        there is, so it is added here under `transports`.
        """
        schema = DisplayConfig.model_json_schema()
        schema["transports"] = {
            name: {
                "description": cls.description,
                "pushes": cls.pushes,
                "options": dict(cls.options_doc),
                # How to ask for each option — which mode it belongs to,
                # whether it is required, what control to draw
                # (`OptionField`, `src/maverick/transports/base.py`). Keys
                # absent here are plain text boxes.
                "fields": {key: field.to_json() for key, field in cls.option_fields.items()},
                "mode_option": cls.mode_option,
            }
            for name, cls in sorted(available_transports().items())
        }
        # What the form calls each setting: a short label, one line of help
        # and whether it is an expert setting (`src/maverick/server/copy.py`).
        # The reference descriptions above stay for the *More* disclosure.
        schema["ui"] = ui_copy()
        return schema

    #: `dashboard_url` asks the Supervisor for the ESPHome add-on; once a
    #: minute is plenty for a fact that changes when someone installs an app.
    esphome_dashboard_cache: dict[str, Any] = {"at": 0.0, "value": None}

    async def _esphome_dashboard() -> dict[str, Any] | None:
        import time

        now = time.monotonic()
        if now - esphome_dashboard_cache["at"] > 60:
            esphome_dashboard_cache["value"] = await esphome_install.dashboard_url(
                application.config.home_assistant.render_url
            )
            esphome_dashboard_cache["at"] = now
        return esphome_dashboard_cache["value"]

    @api.get("/api/environment", dependencies=[auth])
    async def environment() -> dict[str, Any]:
        """What this host can and cannot do, for the forms to draw themselves by.

        `addon` says Maverick runs under the Supervisor, where no Bluetooth
        adapter is ever visible (`app/config.yaml` asks for none), so the
        transport forms leave `mode: ble` out. `bluetooth_scan` says a scan
        from this host could work at all: not in the app, and only with the
        `opendisplay` extra installed. `esphome_dashboard` is the ESPHome
        Device Builder add-on when it is installed, with the page its
        *Install* button is on; `esphome_destinations` is where a generated
        configuration can be written so it appears there
        (`src/maverick/esphome/install.py`).
        """
        addon = supervisor.running_under_supervisor()
        try:
            import opendisplay  # noqa: F401

            extra = True
        except ImportError:
            extra = False
        return {
            "addon": addon,
            "opendisplay_extra": extra,
            "bluetooth_scan": extra and not addon,
            "home_assistant": application.engine.ha_ok,
            "esphome_dashboard": await _esphome_dashboard(),
            "esphome_destinations": [
                d.to_json()
                for d in esphome_install.destinations(application.config.server.esphome_dir)
            ],
        }

    @api.get("/api/ha/opendisplay/devices", dependencies=[auth])
    async def ha_opendisplay_devices() -> list[dict[str, Any]]:
        """Every tag Home Assistant's OpenDisplay integration knows, for a picker.

        The device registry id is what `opendisplay.upload_image` wants, and
        copying it out of a device page URL was the single most common
        mistake in `mode: ha` (`src/maverick/transports/opendisplay.py`).
        Each entry carries a `panel_guess` from its model string
        (`guess_panel`, `src/maverick/devices/guess.py`) and, when a
        configured display already delivers to it, that display's id.
        """
        if not application.engine.ha_ok or application.engine.ha is None:
            raise HTTPException(
                status_code=503,
                detail="Not connected to Home Assistant, so its OpenDisplay tags cannot be listed.",
            )
        try:
            devices = await application.engine.ha.list_devices("opendisplay")
        except ha_client.HomeAssistantError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        in_use = {
            str(getattr(d.transport, "device_id", "") or ""): d.id
            for d in application.config.displays
        }
        for device in devices:
            device["panel_guess"] = guess_panel(
                device.get("model"), device.get("name"), device.get("manufacturer"),
                prefer_transport="opendisplay",
            )
            device["display_id"] = in_use.get(str(device["id"]))
        return devices

    @api.post("/api/opendisplay/scan", dependencies=[auth])
    async def opendisplay_scan(timeout: float = Query(default=10.0, ge=1, le=60)) -> dict[str, Any]:
        """Scan for OpenDisplay tags from this host's own Bluetooth adapter.

        The route `maverick scan` always had and the setup UI never did. It
        cannot work in the app (no adapter; `409`) and needs the `opendisplay`
        extra (`501`); a scan that raises is the adapter's problem and comes
        back as `502` with the library's message.
        """
        if supervisor.running_under_supervisor():
            raise HTTPException(
                status_code=409,
                detail=(
                    "The Home Assistant app has no Bluetooth of its own, so it cannot scan. "
                    "Tags Home Assistant's OpenDisplay integration has found are listed "
                    "instead; use mode: ha."
                ),
            )
        try:
            from ..transports.opendisplay import scan
        except ImportError as exc:
            raise HTTPException(
                status_code=501,
                detail="py-opendisplay is not installed. Install the 'opendisplay' extra.",
            ) from exc
        try:
            found = await scan(timeout=timeout)
        except ImportError as exc:
            raise HTTPException(
                status_code=501,
                detail="py-opendisplay is not installed. Install the 'opendisplay' extra.",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - the adapter's message is the answer
            raise HTTPException(status_code=502, detail=f"BLE scan failed: {exc}") from exc
        return {
            "tags": [
                {
                    "name": name,
                    "mac": mac,
                    "panel_guess": guess_panel(name, prefer_transport="opendisplay"),
                }
                for name, mac in sorted(found.items())
            ]
        }

    def _next_run_at(display_id: str) -> str | None:
        job = next(
            (j for j in application.scheduler.jobs() if j.display_id == display_id), None
        )
        return job.next_run.isoformat() if job and job.next_run else None

    def _display_summary(display_id: str) -> dict[str, Any]:
        display = application.config.display(display_id)
        resolved = display.resolved()
        state = application.engine.states.get(display_id)
        frame = application.engine.frames.get(display_id)
        pulled = application.engine.frames.last_pulled(display_id)
        pages = display.page_entries
        page_index = application.engine.page_index(display_id)
        page = pages[page_index]
        return {
            "id": display.id,
            "name": display.name,
            "enabled": display.enabled,
            # The display's own configuration, in the shortest form that loads
            # back as it (`dump_display`, `src/maverick/store.py`) — which is
            # what a `PUT` to this display takes, so the setup UI can change
            # one key and send the rest back untouched.
            "config": dump_display(display),
            "panel": display.panel,
            "panel_name": resolved.profile.name,
            "dashboard": display.dashboard,
            "width": resolved.width,
            "height": resolved.height,
            "color_scheme": resolved.color_scheme.value,
            "dpi": resolved.dpi,
            "rotation": resolved.rotation,
            "frame_format": resolved.frame_format.value,
            "transport": display.transport_type,
            # Where this page lives in Home Assistant's own frontend, and the
            # same with `?edit=1`, which opens its editor: the card's *Edit in
            # Home Assistant* link. None for a page that is not a Home
            # Assistant path (a full URL, a file).
            "dashboard_url": _frontend_url(application, page.dashboard),
            "edit_url": _frontend_url(application, page.dashboard, edit=True),
            # Whether the card should offer the ESPHome install step at all:
            # a pull display on a panel that is plausibly an ESP32
            # (`esphome_applicable`, `src/maverick/esphome/generator.py`).
            "esphome_applicable": esphome_applicable(resolved),
            # Always present, `pages` or not: a display with none has exactly
            # one page — its `dashboard` — so a client counts pages rather than
            # asking which form the display was written in
            # (`DisplayConfig.page_entries`, `src/maverick/config.py`).
            "page": {
                "index": page_index,
                "name": page.name,
                "dashboard": page.dashboard,
                "count": len(pages),
                "names": [entry.name for entry in pages],
                "rotate": display.rotate,
            },
            "schedule": {
                "enabled": application.scheduler.schedule_enabled.get(display_id, True),
                "every": display.schedule.every,
                "cron": display.schedule.cron,
                "quiet_hours": display.schedule.quiet_hours,
                "on_change": display.schedule.on_change,
            },
            "rendering": application.engine.is_rendering(display_id),
            "next_run_at": _next_run_at(display_id),
            "last_render_s": (
                round(state.last_render_s, 3) if state and state.render_count else None
            ),
            "last_total_s": (
                round(state.last_total_s, 3) if state and state.render_count else None
            ),
            "state": state.__dict__ if state else {},
            "checksum": frame.checksum if frame else None,
            "lint": (
                {
                    "summary": frame.lint_summary,
                    "issues": _with_advice(frame.lint_issues),
                    "metrics": frame.metrics,
                }
                if frame
                else None
            ),
            "last_pulled_at": pulled.isoformat() if pulled else None,
        }

    @api.get("/api/displays", dependencies=[auth])
    async def list_displays() -> list[dict[str, Any]]:
        return [_display_summary(d.id) for d in application.config.displays]

    @api.get("/api/displays/{display_id}", dependencies=[auth])
    async def get_display(display_id: str) -> dict[str, Any]:
        _lookup(application, display_id)
        return _display_summary(display_id)

    @api.get("/api/displays/{display_id}/history", dependencies=[auth])
    async def display_history(
        display_id: str, limit: int = Query(default=20, ge=1, le=HISTORY_LIMIT)
    ) -> list[dict[str, Any]]:
        """Past render outcomes for this display, newest first.

        `Engine.render_history` (`src/maverick/engine.py`) reads from the
        bounded, persisted buffer `Engine._notify` appends to on every render —
        success, failure, a lint block or an unchanged skip — which is what
        lets this answer "what happened" after `DisplayState.last_error` has
        already been cleared by a later success.
        """
        _lookup(application, display_id)
        return application.engine.render_history(display_id, limit=limit)

    @api.post("/api/displays", status_code=201, dependencies=[auth])
    async def create_display(display: DisplayConfig) -> dict[str, Any]:
        """Add a display and start rendering it, with no restart.

        Every model but `TransportConfig` is `extra="forbid"`
        (`src/maverick/config.py`), so a misspelt key such as `panell` fails
        here as a **422** naming the field: FastAPI validates the request body
        against `DisplayConfig` before this function runs, and the
        `ConfigError`s a validator raises are `ValueError`s, which pydantic
        turns into the same kind of error as any other bad field.
        """
        if any(d.id == display.id for d in application.config.displays):
            raise HTTPException(
                status_code=409, detail=f"display {display.id!r} already exists"
            )
        try:
            await application.add_display(display)
        except (ValueError, KeyError) as exc:
            # ValueError: an invalid cron expression, caught only once APScheduler
            # parses it (`RenderScheduler.check`). KeyError: an unregistered
            # transport type — `TransportConfig.type` is a plain `str`
            # (`extra="allow"`, `src/maverick/config.py`), so nothing validates
            # it before `get_transport` looks it up.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _display_summary(display.id)

    @api.put("/api/displays/{display_id}", dependencies=[auth])
    async def replace_display(display_id: str, display: DisplayConfig) -> dict[str, Any]:
        """Full replacement of an existing display's configuration."""
        if display.id != display_id:
            raise HTTPException(
                status_code=400,
                detail=f"body id {display.id!r} does not match path id {display_id!r}",
            )
        _lookup(application, display_id)
        try:
            await application.update_display(display_id, display)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _display_summary(display_id)

    @api.delete("/api/displays/{display_id}", dependencies=[auth])
    async def delete_display(display_id: str) -> Response:
        """Stop rendering a display and delete its stored frames from disk."""
        _lookup(application, display_id)
        await application.remove_display(display_id)
        return Response(status_code=204)

    @api.post("/api/displays/{display_id}/schedule", dependencies=[auth])
    async def set_schedule(display_id: str, body: ScheduleToggle) -> dict[str, Any]:
        """Pause or resume this display's schedule at runtime.

        Distinct from the config-level `enabled` flag, which goes through
        `PUT`: this reuses `Application.handle_command`
        (`src/maverick/app.py`), the same path the MQTT `schedule_on`/
        `schedule_off` commands take, so both publish the same MQTT state.
        """
        _lookup(application, display_id)
        await application.handle_command(
            display_id, "schedule_on" if body.enabled else "schedule_off"
        )
        return _display_summary(display_id)

    @api.post("/api/displays/{display_id}/page", dependencies=[auth])
    async def set_page(
        display_id: str, body: PageSelect, wait: bool = False
    ) -> dict[str, Any]:
        """Put one of a display's pages on the panel and render it.

        The page moves before this returns, so the summary it answers with
        already names the new one; the render it triggers carries the `page`
        trigger and, by default, runs as a background task — a page change
        costs a whole render, and a picker that waits out `render.timeout` is
        a picker nobody uses. `wait=true` holds the response until the render
        finishes, for a caller that wants the outcome.
        """
        _lookup(application, display_id)
        try:
            if body.step is not None:
                application.engine.advance_page(display_id, body.step)
            else:
                application.engine.select_page(
                    display_id, body.index if body.name is None else body.name
                )
        except ValueError as exc:
            # `ConfigError` is a `ValueError` (`src/maverick/config.py`): an
            # index outside the list, or a name the display does not have.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if wait:
            await application.render(display_id, trigger="page")
        else:
            _fire_and_forget(application.render(display_id, trigger="page"))
        return _display_summary(display_id)

    @api.post("/api/displays/preview", dependencies=[auth])
    async def preview_display(display: DisplayConfig) -> dict[str, Any]:
        """Render a candidate config and hand back the frame. Changes nothing.

        `Engine.render_candidate` (`src/maverick/engine.py`) never touches
        `states`, `frames` or the display store, and delivers nothing: this is
        a dry run for an editor's Preview button, not a save.
        """
        outcome = await application.engine.render_candidate(display)
        if outcome.frame is None:
            raise HTTPException(status_code=502, detail=outcome.reason or "render failed")
        buffer = BytesIO()
        outcome.frame.preview.save(buffer, format="PNG")
        screenshot_buffer = BytesIO()
        outcome.screenshot.save(screenshot_buffer, format="PNG")
        return {
            "preview_png": base64.b64encode(buffer.getvalue()).decode("ascii"),
            # The pre-quantisation capture, downscaled the same way
            # `Engine.render` stores it (`fit_to_panel`, `src/maverick/eink/
            # pipeline.py`), so the editor's preview can show source and
            # result side by side.
            "screenshot_png": base64.b64encode(screenshot_buffer.getvalue()).decode("ascii"),
            "width": outcome.frame.width,
            "height": outcome.frame.height,
            "lint": {
                "summary": outcome.frame.lint.summary(),
                "issues": [
                    {
                        "code": i.code,
                        "severity": i.severity.value,
                        "message": i.message,
                        "hint": i.hint,
                        "advice": lint_advice(i.code),
                    }
                    for i in outcome.frame.lint.issues
                ],
                "metrics": dict(outcome.frame.metrics),
            },
            "render_s": round(outcome.render_s, 3),
            "process_s": round(outcome.process_s, 3),
        }

    @api.post("/api/displays/probe", dependencies=[auth])
    async def probe_candidate(display: DisplayConfig) -> dict[str, Any]:
        """Ask a candidate config's transport whether a delivery would arrive.

        The *Test delivery* button: for an OpenDisplay tag in `ha` mode that
        is "does Home Assistant know this device id", in `ble` mode "is the
        tag in range", for `http_pull` "is `server.base_url` set"
        (`Transport.probe`, `src/maverick/transports/`). Nothing is saved and
        no frame is sent; a display of the same id may or may not exist.
        """
        try:
            result = await application.engine.probe_candidate(display)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": result.ok, "detail": result.detail, "pending": result.pending}

    @api.post("/api/displays/{display_id}/probe", dependencies=[auth])
    async def probe_display(display_id: str) -> dict[str, Any]:
        """The same probe for a configured display, as `maverick check` runs it."""
        _lookup(application, display_id)
        try:
            result = await application.engine.probe(display_id)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"ok": result.ok, "detail": result.detail, "pending": result.pending}

    @api.post("/api/displays/{display_id}/render", dependencies=[auth])
    async def render_display(
        display_id: str, force: bool = False, wait: bool = True
    ) -> Response:
        """Render one display now.

        `wait=false` returns **202** immediately and renders as a background
        task, for a caller — the setup UI, behind ingress — that would
        otherwise block for the whole render timeout with nothing to show for
        it. The default, `wait=true`, keeps the synchronous response
        `rest_command` users depend on.
        """
        _lookup(application, display_id)
        if not wait:
            if application.engine.is_rendering(display_id):
                return JSONResponse(
                    {"display": display_id, "queued": False, "already_rendering": True},
                    status_code=202,
                )
            _fire_and_forget(application.render(display_id, trigger="api", force=force))
            return JSONResponse({"display": display_id, "queued": True}, status_code=202)

        outcome = await application.render(display_id, trigger="api", force=force)
        return JSONResponse(
            {
                "display": display_id,
                "ok": outcome.ok,
                "skipped": outcome.skipped,
                "reason": outcome.reason,
                "render_s": round(outcome.render_s, 3),
                "total_s": round(outcome.total_s, 3),
                "checksum": outcome.frame.checksum if outcome.frame else None,
                "lint": outcome.frame.lint.summary() if outcome.frame else None,
                "delivery": outcome.delivery.detail if outcome.delivery else None,
            }
        )

    @api.post("/api/render", dependencies=[auth])
    async def render_all(force: bool = False) -> list[dict[str, Any]]:
        outcomes = await application.render_all(trigger="api", force=force)
        return [
            {"display": o.display_id, "ok": o.ok, "skipped": o.skipped, "reason": o.reason}
            for o in outcomes
        ]

    # ----------------------------------------------- frame delivery (pull) --

    @api.get("/api/displays/{display_id}/frame", dependencies=[auth])
    async def get_frame(display_id: str, request: Request) -> Response:
        """Serve the current frame to a device that fetches on its own schedule."""
        _lookup(application, display_id)
        frame = application.engine.frames.get(display_id)
        if frame is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"no frame rendered yet for {display_id!r}; "
                    f"POST /api/displays/{display_id}/render first"
                ),
            )
        application.engine.frames.mark_pulled(display_id)
        state = application.engine.states.get(display_id)
        if state is not None:
            state.last_pulled_at = datetime.now(UTC).isoformat(timespec="seconds")

        headers = {
            # Note: the ASGI server emits `Date` itself, so we must not add one
            # — a duplicate Date is invalid HTTP and the minimal parsers in
            # e-ink firmware are exactly the clients that mishandle it. Devices
            # still get the server clock from that header and need no SNTP.
            "ETag": frame.etag,
            "Cache-Control": "no-cache",
            "X-Maverick-Checksum": frame.checksum,
            "X-Maverick-Width": str(frame.width),
            "X-Maverick-Height": str(frame.height),
            "X-Maverick-Colors": str(frame.colors),
            "X-Maverick-Next-Refresh": str(_next_refresh_seconds(application, display_id)),
        }

        # A matching ETag means the panel is already showing this frame. Returning
        # 304 saves the download and, far more importantly, the e-ink refresh.
        if request.headers.get("if-none-match", "").strip() == frame.etag:
            return Response(status_code=304, headers=headers)

        return Response(content=frame.payload, media_type=frame.media_type, headers=headers)

    @api.get("/api/displays/{display_id}/preview.png", dependencies=[auth])
    async def preview(display_id: str) -> Response:
        """The frame as a viewable PNG, for the UI and the HA image entity.

        Behind the token like everything else it shows, which is why MQTT
        discovery stops advertising this URL to the Home Assistant image
        entity once a token is set and publishes the bytes over the broker
        instead (`src/maverick/ha/discovery.py`): an image entity fetches with
        no credentials.
        """
        _lookup(application, display_id)
        frame = application.engine.frames.get(display_id)
        if frame is None or not frame.preview_png:
            raise HTTPException(status_code=404, detail="no frame rendered yet")
        return Response(
            content=frame.preview_png,
            media_type="image/png",
            headers={"Cache-Control": "no-cache", "ETag": frame.etag},
        )

    @api.get("/api/displays/{display_id}/screenshot.png", dependencies=[auth])
    async def screenshot(display_id: str) -> Response:
        """The pre-quantisation capture, downscaled to panel resolution.

        Lets the setup UI answer "did the dashboard render wrong, or did the
        pipeline do this" without Samba or SSH access to `<data_dir>/frames/`.
        Stored by `Engine.render` when `render.keep_screenshot` is set
        (`src/maverick/config.py`), independent of the frame a transport
        delivers, so it exists for every transport once a display has
        rendered — not only for `http_pull`, the one transport that ever
        calls `FrameStore.put` (`src/maverick/transports/pull.py`). 404
        before the first render, and always when the flag is off.
        """
        _lookup(application, display_id)
        data = application.engine.frames.get_screenshot(display_id)
        if not data:
            raise HTTPException(status_code=404, detail="no screenshot rendered yet")
        return Response(
            content=data,
            media_type="image/png",
            headers={"Cache-Control": "no-cache"},
        )

    @api.get(
        "/api/displays/{display_id}/esphome.yaml",
        response_class=PlainTextResponse,
        dependencies=[auth],
    )
    async def esphome_config(display_id: str) -> str:
        """A ready-to-flash ESPHome config for this display.

        Secrets are `!secret` references, the token included
        (`src/maverick/esphome/generator.py`); the JSON route below carries
        the values Maverick knows.
        """
        display = _lookup(application, display_id)
        return describe_esphome(display.resolved(), application.config)["yaml"]

    @api.get("/api/displays/{display_id}/esphome", dependencies=[auth])
    async def esphome_describe(display_id: str) -> dict[str, Any]:
        """The generated config plus everything the *Install on device* step shows.

        The YAML, the node name (the file name ESPHome expects), the secrets
        the file references — with the value of Maverick's own token, which
        is why this route sits behind the token — whether the panel has a
        driver ESPHome knows by name, whether the frame needs PSRAM, where
        the file can be written so the ESPHome Device Builder sees it, and
        the Device Builder's own page when the add-on is installed
        (`describe_esphome`, `src/maverick/esphome/generator.py`;
        `src/maverick/esphome/install.py`).
        """
        display = _lookup(application, display_id)
        resolved = display.resolved()
        described = describe_esphome(resolved, application.config)
        names = [entry["name"] for entry in described["secrets"]]
        targets = []
        for target in esphome_install.destinations(application.config.server.esphome_dir):
            entry = target.to_json()
            entry["missing_secrets"] = esphome_install.missing_secrets(target.path, names)
            entry["installed"] = (target.path / described["filename"]).is_file()
            targets.append(entry)
        described["applicable"] = esphome_applicable(resolved)
        described["destinations"] = targets
        described["dashboard"] = await _esphome_dashboard()
        return described

    @api.post("/api/displays/{display_id}/esphome/install", dependencies=[auth])
    async def esphome_install_route(display_id: str, body: EsphomeInstall) -> dict[str, Any]:
        """Write the generated config where the ESPHome Device Builder reads it.

        `destination` is one of the ids `GET .../esphome` listed. A file that
        already exists there with different content is the user's — the
        generated file is theirs to edit — so it is replaced only with
        `overwrite: true`, and the route answers **409** otherwise.
        `secrets.yaml` beside it is read to say which names are still
        missing, and never written: it holds the Wi-Fi password.
        """
        display = _lookup(application, display_id)
        described = describe_esphome(display.resolved(), application.config)
        try:
            target = esphome_install.destination(
                body.destination, application.config.server.esphome_dir
            )
            path = esphome_install.install(
                target, described["filename"], described["yaml"], overwrite=body.overwrite
            )
        except esphome_install.ExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except esphome_install.InstallError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        names = [entry["name"] for entry in described["secrets"]]
        return {
            "path": str(path),
            "destination": target.to_json(),
            "missing_secrets": esphome_install.missing_secrets(target.path, names),
            "dashboard": await _esphome_dashboard(),
        }

    @api.get(
        "/api/displays/{display_id}/dashboard.yaml",
        response_class=PlainTextResponse,
        dependencies=[auth],
    )
    async def starter_dashboard(display_id: str) -> str:
        """A Lovelace dashboard sized for this display's panel.

        The gap this fills is the one at the very start: Maverick renders a
        dashboard you already have, and the dashboard you already have was
        built for a phone. This hands back one built for *this* panel — the
        column count and the line budget from its own px and dpi
        (`src/maverick/eink/layout.py`), and only cards that survive
        quantisation.

        The entity list is fetched from Home Assistant so the result names
        entities that exist. A failure there is not an error: the generator
        falls back to placeholder ids and says so in the file, because a
        layout with the wrong entity names is still the right layout, and a
        503 here would be the second thing to go wrong for a user whose
        credential is what went wrong first.
        """
        from ..lovelace import generate_dashboard

        display = _lookup(application, display_id)
        states = None
        if application.engine.ha_ok and application.engine.ha is not None:
            try:
                states = await application.engine.ha.list_states()
            except (ha_client.HomeAssistantError, httpx.HTTPError):
                states = None
        return generate_dashboard(display.resolved(), states)

    @api.post("/api/displays/{display_id}/dashboard/create", dependencies=[auth])
    async def create_starter_dashboard(display_id: str, body: DashboardCreate) -> dict[str, Any]:
        """Create the starter dashboard in Home Assistant and point the display at it.

        The copy-and-paste loop, done by the server: the same starter
        `GET .../dashboard.yaml` hands back is created as a storage-mode
        dashboard called `maverick-<id>` through `lovelace/dashboards/create`
        and filled through `lovelace/config/save`
        (`HomeAssistantClient.create_dashboard` and `.save_dashboard_config`,
        `src/maverick/ha/client.py`), and the display's `dashboard` is set to
        its view. A dashboard already at that `url_path` is the user's — they
        may have edited it — so it is replaced only with `overwrite: true`
        and the route answers **409** otherwise. A display with `pages` gets
        the dashboard but keeps its pages: `dashboard` and `pages` are
        exclusive (`DisplayConfig`, `src/maverick/config.py`), and the
        response says `applied: false`.

        Both commands are admin-only in Home Assistant, so a credential
        without that right fails here with Home Assistant's own message as a
        **503**, and nothing is half done: the create runs before the save,
        and a failed create leaves no dashboard to fill.
        """
        from ..lovelace import (
            dashboard_url_path,
            generate_dashboard_config,
            starter_view_path,
        )

        display = _lookup(application, display_id)
        ha = application.engine.ha
        if not application.engine.ha_ok or ha is None:
            raise HTTPException(
                status_code=503,
                detail="Not connected to Home Assistant, so no dashboard can be created there.",
            )
        resolved = display.resolved()
        url_path = dashboard_url_path(resolved)
        title = display.name
        try:
            states = await ha.list_states()
        except (ha_client.HomeAssistantError, httpx.HTTPError):
            states = None
        config = generate_dashboard_config(resolved, states)
        try:
            existing = url_path in await ha.dashboard_url_paths()
            if existing and not body.overwrite:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Home Assistant already has a dashboard at /{url_path}. Replace "
                        "its contents with a fresh starter, or leave it as it is."
                    ),
                )
            if not existing:
                await ha.create_dashboard(url_path, title)
            await ha.save_dashboard_config(url_path, config)
        except ha_client.HomeAssistantError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        view_path = starter_view_path(resolved)
        applied = False
        if not display.pages:
            updated = DisplayConfig.model_validate(
                {**dump_display(display), "dashboard": view_path}
            )
            await application.update_display(display_id, updated)
            applied = True
        return {
            "url_path": url_path,
            "path": view_path,
            "title": title,
            "created": not existing,
            "replaced": existing,
            "applied": applied,
            "open_url": _frontend_url(application, view_path),
            "edit_url": _frontend_url(application, view_path, edit=True),
        }

    # ------------------------------------------------------- TRMNL (BYOS) --

    @api.get("/api/setup")
    async def trmnl_setup(request: Request) -> JSONResponse:
        """TRMNL bring-your-own-server handshake.

        TRMNL firmware calls this once with its MAC in the ``ID`` header and
        expects an API key back. Maverick matches the MAC against a display's
        ``transport.mac``, so a TRMNL panel needs no extra configuration.
        """
        mac = request.headers.get("id", "").upper()
        display = _display_for_mac(application, mac)
        if display is None:
            return JSONResponse({"status": 404, "message": f"no display for MAC {mac}"}, 404)
        return JSONResponse(
            {
                "status": 200,
                "api_key": application.config.server.api_token or display.id,
                "friendly_id": display.id[:6].upper(),
                "image_url": _frame_url(application, display.id),
                "message": "welcome to maverick",
            }
        )

    @api.get("/api/display")
    async def trmnl_display(request: Request) -> JSONResponse:
        mac = request.headers.get("id", "").upper()
        display = _display_for_mac(application, mac)
        if display is None:
            return JSONResponse({"status": 404}, 404)
        stored = application.engine.frames.get(display.id)
        return JSONResponse(
            {
                "status": 0,
                "image_url": _frame_url(application, display.id),
                "filename": f"{display.id}-{stored.checksum if stored else 'pending'}",
                "refresh_rate": _next_refresh_seconds(application, display.id),
                "reset_firmware": False,
                "update_firmware": False,
            }
        )

    # --------------------------------------------------------------- link --
    #
    # The IndieAuth flow that gets Maverick a Home Assistant credential without
    # the user copying a secret between two pages. Three steps: report what we
    # have, bounce the browser to Home Assistant, take the code back.

    #: Outstanding authorization attempts, nonce -> the client_id it was started
    #: with. Home Assistant requires the token exchange to present the same
    #: client_id as the authorize step, and `server.base_url` could change in
    #: between, so it is remembered rather than recomputed. Kept in memory on
    #: purpose: a nonce that does not survive a restart cannot be replayed
    #: after one.
    pending_links: dict[str, str] = {}

    @api.get("/api/auth/status")
    async def auth_status() -> dict[str, Any]:
        ha = application.config.home_assistant
        source = application.engine.tokens
        base_url = application.config.server.base_url
        reason = ""
        if not base_url:
            reason = (
                "server.base_url is not set, so Maverick does not know which URL "
                "to ask Home Assistant to redirect back to."
            )
        else:
            try:
                ha_auth.client_id_for(base_url)
            except ha_auth.AuthError as exc:
                reason = str(exc)
        return {
            "linked": source is not None,
            "kind": source.kind if source else "none",
            # Whether Home Assistant answered, not whether a credential is
            # configured: `engine.ha` is set for a credential it rejects too.
            "connected": application.engine.ha_ok,
            "url": ha.url,
            "can_link": not reason,
            "reason": reason,
            "client_id": ha_auth.client_id_for(base_url) if not reason else "",
            "persists": supervisor.running_under_supervisor(),
        }

    @api.get("/api/auth/start", dependencies=[auth])
    async def auth_start() -> Response:
        base_url = application.config.server.base_url
        if not base_url:
            raise HTTPException(
                status_code=400,
                detail=(
                    "server.base_url must be set before linking, because Home "
                    "Assistant redirects back to it. In the app, set the "
                    "base_url option."
                ),
            )
        try:
            client_id = ha_auth.client_id_for(base_url)
            redirect_uri = ha_auth.redirect_uri_for(base_url)
        except ha_auth.AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        import secrets

        nonce = secrets.token_urlsafe(24)
        # One attempt at a time; a stale nonce from an abandoned attempt would
        # otherwise stay valid indefinitely.
        pending_links.clear()
        pending_links[nonce] = client_id
        target = ha_auth.authorize_url(
            application.config.home_assistant.url, client_id, redirect_uri, nonce
        )
        return JSONResponse({"authorize_url": target})

    @api.get("/api/auth/callback", response_class=HTMLResponse)
    async def auth_callback(
        code: str = "", state: str = "", error: str = ""
    ) -> HTMLResponse:
        """Where Home Assistant sends the browser back.

        Home Assistant knows nothing of ``server.api_token``, so this endpoint
        cannot sit behind it. The ``state`` nonce is what authenticates the
        callback: it was minted by ``/api/auth/start``, which *is* behind the
        token, and it is spent on first use.
        """
        if error:
            return _link_result(f"Home Assistant refused the request: {error}", False)
        client_id = pending_links.pop(state, None)
        if client_id is None:
            return _link_result(
                "This link attempt is not one Maverick started, or it has already "
                "been used. Start again from the setup UI.",
                False,
            )
        if not code:
            return _link_result("Home Assistant returned no authorization code.", False)

        ha = application.config.home_assistant
        try:
            grant = await ha_auth.exchange_code(
                ha.url, client_id, code, verify_ssl=ha.verify_ssl
            )
        except ha_auth.AuthError as exc:
            return _link_result(str(exc), False)

        # Apply first, persist second: a credential that works but was not
        # written down is recoverable by linking again, whereas one written
        # down without being checked leaves a broken app that looks configured.
        ha.refresh_token = grant.refresh_token
        ha.client_id = client_id
        ha.token = ""
        try:
            await application.engine.relink()
        except Exception as exc:  # noqa: BLE001 - reported to the user's browser
            return _link_result(f"Linked, but Home Assistant rejected it: {exc}", False)

        note = ""
        if supervisor.running_under_supervisor():
            try:
                await supervisor.save_options(
                    {
                        "home_assistant_refresh_token": grant.refresh_token,
                        "home_assistant_client_id": client_id,
                        "home_assistant_token": "",
                    }
                )
            except supervisor.SupervisorError as exc:
                log.warning("could not save the credential to the app options: %s", exc)
                note = (
                    "The link works now, but it could not be saved to the app "
                    f"options ({exc}), so it will be lost on restart."
                )
        else:
            # Not under the Supervisor, so there is no options store to write
            # to. The user owns the config file; show them what to put in it.
            note = (
                "Maverick is not running as a Home Assistant app, so there is "
                "nowhere to save this automatically. To keep the link across "
                "restarts, put this in your config file under home_assistant:\n"
                f"  refresh_token: {grant.refresh_token}\n"
                f"  client_id: {client_id}"
            )
        return _link_result("Linked to Home Assistant.", True, note)

    # ----------------------------------------------------------------- UI --

    # Not behind `auth`: these two files are the same for everyone and carry
    # no state, and the token prompt — served precisely when the browser has
    # no accepted token — loads them too. Starlette serves them; nothing here
    # generates them.
    api.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @api.get("/", response_class=HTMLResponse)
    async def index(request: Request, response: Response) -> str:
        if not _authenticated(application, request):
            # 401 like any other gated route, but as a page rather than the
            # dependency's JSON: a browser navigating here cannot send a
            # header, so the reply has to be something a person can act on.
            response.status_code = 401
            return render_token_prompt(supplied=bool(request.query_params.get("token")))
        if not application.config.server.enable_ui:
            return (
                "<h1>Maverick</h1>"
                "<p>The UI is disabled. See <a href='api/docs'>/api/docs</a>.</p>"
            )
        # The same payload `GET /api/displays` answers with, embedded in the
        # page so the first paint has content; `static/app.js` polls that route
        # for every one after it.
        return render_ui(
            application, [_display_summary(d.id) for d in application.config.displays]
        )

    return api


# --------------------------------------------------------------- helpers --

def _link_result(message: str, ok: bool, note: str = "") -> HTMLResponse:
    """The page Home Assistant's redirect lands on.

    Standalone rather than part of the setup UI: it is reached by a redirect
    from another origin, and it has to say something useful even when the whole
    reason the user is here is that nothing is connected yet.
    """
    import html as _html

    colour = "#2c6e3f" if ok else "#a3271f"
    # pre-wrap because the standalone note carries the YAML to paste, and HTML
    # would otherwise collapse it onto one line.
    body = (
        f"<p style='white-space:pre-wrap'>{_html.escape(note)}</p>" if note else ""
    )
    return HTMLResponse(
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Maverick</title>"
        "<body style=\"font:15px/1.6 ui-sans-serif,system-ui,sans-serif;"
        'margin:0;padding:48px 24px;max-width:38em">'
        f"<h1 style=\"font-size:20px;color:{colour}\">{_html.escape(message)}</h1>"
        f"{body}"
        "<p><a href='/'>Back to Maverick</a></p></body>",
        status_code=200 if ok else 400,
    )


def _fire_and_forget(coro: Awaitable[Any]) -> asyncio.Task[Any]:
    """Run a coroutine as a background task, logging a failure rather than losing it.

    Used for ``?wait=false`` renders: the request has already answered 202, so
    the log is the only place left to report a failure.
    """
    task = asyncio.create_task(coro)

    def _log_if_failed(finished: asyncio.Task[Any]) -> None:
        if finished.cancelled():
            return
        if (exc := finished.exception()) is not None:
            log.error("background render failed: %s", exc)

    task.add_done_callback(_log_if_failed)
    return task


def _lookup(application: Application, display_id: str):
    try:
        return application.config.display(display_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _next_refresh_seconds(application: Application, display_id: str) -> int:
    """How long a device may sleep before it should ask again.

    Falls back to 15 minutes for cron schedules, where "time until next run" is
    knowable but the device only needs a safe upper bound.
    """
    schedule = application.config.display(display_id).schedule
    if schedule.interval_seconds:
        return int(schedule.interval_seconds)
    return 900


def _frontend_url(application: Application, dashboard: str, *, edit: bool = False) -> str | None:
    """Where a display's page is in Home Assistant's frontend, or None.

    A dashboard path (`/lovelace-eink/kitchen`) is joined to
    `home_assistant.render_url`, the frontend origin the renderer itself
    loads pages from (`src/maverick/config.py`). `edit=True` appends
    `?edit=1`, which the frontend takes as "open this view in the editor".
    A full URL is handed back as it is, and a page that is not a Home
    Assistant one (a `file://` page, say) gets None: there is no editor for it.
    """
    if not dashboard:
        return None
    if dashboard.startswith("/"):
        url = application.config.home_assistant.render_url + dashboard
    elif dashboard.startswith(("http://", "https://")):
        url = dashboard
    else:
        return None
    if edit:
        if not url.startswith(application.config.home_assistant.render_url):
            return None
        url += ("&" if "?" in url else "?") + "edit=1"
    return url


def _with_advice(issues: Any) -> Any:
    """The stored lint findings with a plain sentence of advice on each."""
    if not isinstance(issues, list):
        return issues
    out = []
    for issue in issues:
        if isinstance(issue, dict):
            out.append({**issue, "advice": lint_advice(str(issue.get("code", "")))})
        else:
            out.append(issue)
    return out


def _frame_url(application: Application, display_id: str) -> str:
    base = (application.config.server.base_url or "").rstrip("/")
    return f"{base}/api/displays/{display_id}/frame"


def _display_for_mac(application: Application, mac: str):
    if not mac:
        return None
    for display in application.config.enabled_displays:
        configured = str(getattr(display.transport, "mac", "") or "").upper()
        if configured and configured.replace("-", ":") == mac.replace("-", ":"):
            return display
    return None


__all__ = ["create_app"]
