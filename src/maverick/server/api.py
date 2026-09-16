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

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..app import VERSION, Application
from ..config import DisplayConfig
from ..devices import all_panels
from ..engine import HISTORY_LIMIT
from ..ha import auth as ha_auth
from ..ha import client as ha_client
from ..ha import supervisor
from ..store import dump_display
from ..transports import available_transports
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
            }
            for name, cls in sorted(available_transports().items())
        }
        return schema

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
            "transport": display.transport.type,
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
                    "issues": frame.lint_issues,
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
                    }
                    for i in outcome.frame.lint.issues
                ],
                "metrics": dict(outcome.frame.metrics),
            },
            "render_s": round(outcome.render_s, 3),
            "process_s": round(outcome.process_s, 3),
        }

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
        """A ready-to-flash ESPHome config for this display."""
        from ..esphome import generate_esphome_config

        display = _lookup(application, display_id)
        return generate_esphome_config(display.resolved(), application.config)

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
