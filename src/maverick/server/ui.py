"""The setup and monitoring UI.

Deliberately one self-contained page with no build step and no dependencies:
this runs as a Home Assistant add-on on hardware as small as a Pi 3, and a
bundler would be more machinery than the page is worth.

What it is *for* is the feedback loop. Tuning an e-ink dashboard means
re-rendering, looking at the result, adjusting a threshold and going again —
and doing that by walking to the panel is miserable. The page shows what each
panel is currently displaying, what the linter thought of it, and a button to
re-render.
"""

from __future__ import annotations

import html
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..app import Application

_CSS = """
:root{--bg:#f6f6f4;--fg:#16161a;--muted:#6b6b76;--line:#d8d8d2;--card:#fff;
      --warn:#a8631a;--err:#a3271f;--ok:#2c6e3f}
@media (prefers-color-scheme:dark){:root{--bg:#16161a;--fg:#ececf0;--muted:#9a9aa6;
      --line:#2e2e36;--card:#1e1e24;--warn:#e0a458;--err:#e0685e;--ok:#6fbf87}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:20px 24px;border-bottom:1px solid var(--line);display:flex;
       align-items:baseline;gap:14px;flex-wrap:wrap}
h1{margin:0;font-size:20px;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13px}
main{padding:24px;display:grid;gap:20px;
     grid-template-columns:repeat(auto-fill,minmax(340px,1fr));max-width:1500px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
      overflow:hidden;display:flex;flex-direction:column}
.card h2{margin:0;padding:14px 16px 10px;font-size:16px;display:flex;
         justify-content:space-between;align-items:center;gap:8px}
.meta{padding:0 16px 12px;color:var(--muted);font-size:12.5px}
.shot{background:#e9e7e0;border-top:1px solid var(--line);
      border-bottom:1px solid var(--line);display:block;width:100%;height:auto}
.row{padding:12px 16px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
button{font:inherit;padding:6px 12px;border:1px solid var(--line);border-radius:6px;
       background:var(--bg);color:var(--fg);cursor:pointer}
button:hover{border-color:var(--muted)} button:disabled{opacity:.5;cursor:wait}
.pill{font-size:11.5px;padding:2px 8px;border-radius:99px;border:1px solid var(--line)}
.ok{color:var(--ok)} .warn{color:var(--warn)} .err{color:var(--err)}
.issues{padding:0 16px 14px;margin:0;font-size:12.5px;list-style:none}
.issues li{padding:6px 0;border-top:1px dotted var(--line)}
.issues .hint{color:var(--muted)}
code{font:12px ui-monospace,monospace;background:var(--bg);padding:1px 5px;
     border-radius:4px;border:1px solid var(--line)}
a{color:inherit}
"""

_JS = """
async function post(url){
  const r = await fetch(url, {method:'POST', headers: authHeaders()});
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}
function authHeaders(){
  const t = new URLSearchParams(location.search).get('token');
  return t ? {'Authorization': 'Bearer ' + t} : {};
}
// The authorize URL is fetched rather than linked so the API token (when one
// is set) travels in a header, and so a misconfigured base_url reports itself
// here instead of on a Home Assistant error page.
async function startLink(btn){
  btn.disabled = true; btn.textContent = 'Opening Home Assistant…';
  try {
    const r = await fetch('/api/auth/start', {headers: authHeaders()});
    const body = await r.json();
    if(!r.ok) throw new Error(body.detail || 'could not start linking');
    location.href = body.authorize_url;
  } catch (e) {
    btn.disabled = false; btn.textContent = 'Link with Home Assistant';
    alert(e.message);
  }
}
async function refresh(id, force, btn){
  btn.disabled = true; const label = btn.textContent; btn.textContent = 'Rendering…';
  try {
    const res = await post(`/api/displays/${id}/render?force=${force}`);
    btn.textContent = res.skipped ? 'Unchanged' : (res.ok ? 'Done' : 'Failed');
    // Bust the cache: the preview URL is stable but its content is not.
    const img = document.getElementById('shot-' + id);
    if (img) img.src = `/api/displays/${id}/preview.png?t=${Date.now()}`;
    setTimeout(() => { btn.textContent = label; btn.disabled = false; location.reload(); }, 1200);
  } catch (e) {
    btn.textContent = 'Error'; console.error(e);
    setTimeout(() => { btn.textContent = label; btn.disabled = false; }, 2500);
  }
}
"""


def render_ui(application: Application) -> str:
    config = application.config
    cards = []
    for display in config.displays:
        resolved = display.resolved()
        state = application.engine.states.get(display.id)
        frame = application.engine.frames.get(display.id)

        if state and state.consecutive_failures:
            status_class, status_text = "err", "error"
        elif frame and any(i["severity"] == "warning" for i in frame.lint_issues):
            status_class, status_text = "warn", frame.lint_summary
        elif frame:
            status_class, status_text = "ok", "ok"
        else:
            status_class, status_text = "", "not rendered"

        schedule_bits = []
        if display.schedule.every:
            schedule_bits.append(f"every {display.schedule.every}")
        if display.schedule.cron:
            schedule_bits.append(f"cron {display.schedule.cron}")
        if display.schedule.quiet_hours:
            schedule_bits.append(f"quiet {display.schedule.quiet_hours}")
        if display.schedule.on_change:
            schedule_bits.append(f"{len(display.schedule.on_change)} triggers")

        issues = ""
        if frame and frame.lint_issues:
            items = "".join(
                f"<li><span class='{_sev_class(i['severity'])}'>"
                f"{html.escape(i['severity'])}</span> "
                f"{html.escape(i['message'])}"
                + (f"<div class='hint'>{html.escape(i['hint'])}</div>" if i.get("hint") else "")
                + "</li>"
                for i in frame.lint_issues
            )
            issues = f"<ul class='issues'>{items}</ul>"

        shot = (
            f"<img class='shot' id='shot-{html.escape(display.id)}' "
            f"src='/api/displays/{html.escape(display.id)}/preview.png' "
            f"alt='current frame for {html.escape(display.name)}' loading='lazy'>"
            if frame
            else (
                "<div class='shot' style='padding:40px;text-align:center;color:#888'>"
                "no frame yet</div>"
            )
        )

        error_line = ""
        if state and state.last_error:
            error_line = f"<div class='meta err'>{html.escape(state.last_error[:300])}</div>"

        cards.append(
            f"""
<section class="card">
  <h2>{html.escape(display.name)}
    <span class="pill {status_class}">{html.escape(status_text)}</span></h2>
  <div class="meta">
    <code>{html.escape(display.id)}</code> &middot; {html.escape(resolved.profile.name)}<br>
    {resolved.width}&times;{resolved.height} &middot; {html.escape(resolved.color_scheme.value)}
    &middot; {resolved.dpi} dpi &middot; rot {resolved.rotation}&deg;<br>
    <span class="muted">{html.escape(display.dashboard)}</span><br>
    via <code>{html.escape(display.transport.type)}</code>
    {("&middot; " + html.escape(", ".join(schedule_bits))) if schedule_bits else ""}
  </div>
  {shot}
  <div class="row">
    <button onclick="refresh('{html.escape(display.id)}', false, this)">Refresh</button>
    <button onclick="refresh('{html.escape(display.id)}', true, this)">Full refresh</button>
    <a href="/api/displays/{html.escape(display.id)}/esphome.yaml">ESPHome config</a>
  </div>
  {error_line}
  {issues}
</section>"""
        )

    if not cards:
        cards.append(
            "<section class='card'><h2>No displays configured</h2>"
            "<div class='meta'>Add a <code>displays:</code> entry to your config "
            "and restart. See the "
            "<a href='https://github.com/ambient-home-systems/maverick-eink-dashboard"
            "#configuration'>docs</a>."
            "</div></section>"
        )

    ha_connected = bool(application.engine.ha)
    mqtt_connected = bool(application.engine.mqtt and application.engine.mqtt.connected)
    ha_state = "connected" if ha_connected else "not connected"
    mqtt_state = "connected" if mqtt_connected else "off"

    # The link panel goes first: with no credential nothing else on the page
    # can work, and "not connected" in the header is not an instruction.
    if not ha_connected:
        cards.insert(0, _link_card(application))

    summary = json.dumps(
        {
            "displays": len(config.displays),
            "home_assistant": ha_connected,
            "mqtt": mqtt_connected,
        }
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Maverick</title><style>{_CSS}</style></head>
<body>
<header>
  <h1>Maverick</h1>
  <span class="sub">{len(config.displays)} display(s)
    &middot; Home Assistant {ha_state}
    &middot; MQTT {mqtt_state}
  </span>
  <span class="sub" style="margin-left:auto"><a href="/api/docs">API docs</a></span>
</header>
<main>{"".join(cards)}</main>
<script>const SUMMARY={summary};{_JS}</script>
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
            "back to Maverick, and that is the address it will use."
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


def _sev_class(severity: str) -> str:
    return {"error": "err", "warning": "warn", "info": "muted"}.get(severity, "")


__all__ = ["render_ui"]
