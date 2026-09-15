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

import hmac
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from ..app import VERSION, Application
from ..devices import all_panels
from ..ha import auth as ha_auth
from ..ha import supervisor
from ..transports import available_transports
from .ui import render_token_prompt, render_ui

log = logging.getLogger(__name__)


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
        return [
            {
                "id": p.id, "name": p.name, "vendor": p.vendor,
                "width": p.width, "height": p.height,
                "color_scheme": p.color_scheme.value, "dpi": p.dpi,
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

    # ----------------------------------------------------------- displays --

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

    @api.post("/api/displays/{display_id}/render", dependencies=[auth])
    async def render_display(display_id: str, force: bool = False) -> dict[str, Any]:
        _lookup(application, display_id)
        outcome = await application.render(display_id, trigger="api", force=force)
        return {
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
        return render_ui(application)

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
