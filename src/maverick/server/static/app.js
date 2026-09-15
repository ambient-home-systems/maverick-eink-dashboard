/* The setup UI's script, served from `/static` by the mount in
 * `src/maverick/server/api.py`.
 *
 * A plain ES module: no framework, no build step, nothing from a CDN, because
 * Maverick runs on a LAN that may have no internet.
 *
 * Two pages load it. The setup UI hands it the first `GET /api/displays`
 * payload in a `<script type="application/json">` block so the first paint has
 * content, then lets the poll below keep the cards current in place. The token
 * prompt imports `runTokenPrompt` and nothing else.
 *
 * Every URL here is *document-relative* — `api/...`, never `/api/...`. Home
 * Assistant's ingress serves the page at `/api/hassio_ingress/<token>/` and
 * proxies to the app with that prefix stripped, telling the app nothing about
 * it, so a root-relative URL resolves against Home Assistant's own origin and
 * never arrives (`src/maverick/server/ui.py`, `tests/test_setup_ui.py`).
 */

// --------------------------------------------------------------- the token --

// sessionStorage, never localStorage: the token should not outlive the tab,
// and localStorage would hand it to whoever opens this browser next. Both
// throw outright in a private window, so every access is wrapped — a browser
// that refuses to store it still works, it just asks again next time.
const TOKEN_KEY = 'maverick.api_token';

function storedToken() {
  try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; }
}

function storeToken(value) {
  try { sessionStorage.setItem(TOKEN_KEY, value); } catch (e) { /* private window */ }
}

// `?token=` keeps working and wins: it is how someone arrives with a fresh
// token after the stored one stopped being accepted.
function apiToken() {
  return new URLSearchParams(location.search).get('token') || storedToken();
}

function authHeaders() {
  const token = apiToken();
  return token ? { Authorization: 'Bearer ' + token } : {};
}

// Neither an <img> nor a plain anchor can send a header, so those two present
// the token the other way the server accepts: `?token=` in the query string
// (`_authenticated` in `src/maverick/server/api.py`).
function withToken(url) {
  const token = apiToken();
  if (!token) return url;
  return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token);
}

// Every fetch on these pages goes through here, so a token that is missing,
// wrong or no longer accepted surfaces as the field in the header rather than
// as a button that silently does nothing.
async function authFetch(url, options) {
  const settings = Object.assign({}, options || {});
  settings.headers = Object.assign({}, settings.headers || {}, authHeaders());
  const response = await fetch(url, settings);
  if (response.status === 401) {
    askForToken();
    const error = new Error('Maverick needs its API token.');
    error.unauthorised = true;
    throw error;
  }
  if (!response.ok) throw new Error(await describe(response));
  return response;
}

/** The most useful sentence a failed response has in it. */
async function describe(response) {
  try {
    const body = await response.json();
    if (body && body.detail) return typeof body.detail === 'string'
      ? body.detail
      : JSON.stringify(body.detail);
  } catch (e) { /* not JSON */ }
  return response.status + ' ' + response.statusText;
}

function askForToken() {
  const box = document.getElementById('token-box');
  if (!box) return;
  box.hidden = false;
  const field = document.getElementById('token-field');
  if (field) field.focus();
}

function applyToken(event) {
  const form = event.currentTarget;
  const field = form.querySelector('input');
  const value = (field.value || '').trim();
  event.preventDefault();
  if (!value) return;
  storeToken(value);
  // A document request cannot carry a header, so the page is re-opened with
  // the token in the query, which the server accepts too. Letting the form
  // submit itself would do the same thing, which is what happens if this
  // module never loaded.
  location.search = 'token=' + encodeURIComponent(value);
}

/**
 * The token prompt page's whole behaviour (`render_token_prompt`).
 *
 * `rejected` says the request that got this page already carried a token. It
 * is wrong, so forget it: offering it again is what would turn the redirect
 * below into a loop.
 */
export function runTokenPrompt(rejected) {
  if (rejected) {
    storeToken('');
    return;
  }
  // Arriving here with nothing in the query — a link, a bookmark — the tab may
  // still know the token. Use it rather than asking a question that has
  // already been answered.
  const known = storedToken();
  if (known) location.search = 'token=' + encodeURIComponent(known);
}

// The authorize URL is fetched rather than linked so the API token (when one
// is set) travels in a header, and so a misconfigured base_url reports itself
// here instead of on a Home Assistant error page.
async function startLink(button) {
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  button.textContent = 'Opening Home Assistant…';
  try {
    const response = await authFetch('api/auth/start');
    const body = await response.json();
    // Top-level rather than inside Home Assistant's ingress iframe: the
    // callback lands on the app's own base_url, a different origin, and the
    // result page is worth seeing full width. Falls back to this frame if the
    // browser refuses the top-level navigation.
    try { window.top.location.href = body.authorize_url; }
    catch (e) { location.href = body.authorize_url; }
  } catch (error) {
    button.disabled = false;
    button.removeAttribute('aria-busy');
    button.textContent = 'Link with Home Assistant';
    report(error);
  }
}

// The link card is rendered server-side so that it works with no token and no
// JSON (`src/maverick/server/ui.py`), and its button names this function in an
// inline `onclick`, which can only see globals.
window.startLink = startLink;

// ---------------------------------------------------------------- the poll --

const POLL_MS = 5000;

// `rendering` only goes true once the render holds the display's lock
// (`Engine.is_rendering`, `src/maverick/engine.py`), a moment after the 202
// comes back, so the poll that follows a click can still say "no". `pending`
// covers that gap, and gives up after this long so a render that never starts
// cannot leave a button saying "Rendering…" for ever.
const PENDING_GRACE_MS = 10000;

/** The last `GET /api/displays` payload; every card is painted from it. */
let state = [];
/** display id -> the <section> showing it, so a poll updates instead of rebuilds. */
const cards = new Map();
/** display id -> {at, seen} for a render this page asked for. */
const pending = new Map();
let order = '';
let timer = null;
let stopped = false;
/** Whether the notice is showing a poll failure, and so is ours to clear. */
let polling_failed = false;

async function poll() {
  if (document.hidden) return;
  try {
    const response = await authFetch('api/displays');
    state = await response.json();
    paintAll();
    // Only what the poll itself put there: a failed action's message is the
    // answer to something the user just did, and should outlive the next tick.
    if (polling_failed) {
      notice('');
      polling_failed = false;
    }
  } catch (error) {
    // A 401 has already revealed the token field, and polling on would only
    // ask again every five seconds; anything else is worth saying once.
    if (error.unauthorised) stopped = true;
    report(error);
    polling_failed = true;
  }
}

function schedulePoll() {
  clearTimeout(timer);
  timer = stopped || document.hidden ? null : setTimeout(pollAgain, POLL_MS);
}

async function pollAgain() {
  await poll();
  schedulePoll();
}

// ------------------------------------------------------------- the actions --

/** Run a button's action, busy while it is in flight, then repaint from the API. */
async function act(button, work) {
  notice('');
  setBusy(button, true);
  try {
    await work();
    await poll();
  } catch (error) {
    report(error);
  } finally {
    setBusy(button, false);
    // The poll above knows better than the line above it: a render that is
    // still running puts its buttons straight back to "Rendering…".
    paintAll();
  }
}

function setBusy(button, busy) {
  button.disabled = busy;
  if (busy) button.setAttribute('aria-busy', 'true');
  else button.removeAttribute('aria-busy');
}

function requestRender(id, force, button) {
  return act(button, async () => {
    // `wait=false`: the request answers 202 and the render happens in the
    // background, so a slow dashboard does not hold the click open for the
    // whole render timeout. The poll reports when it finishes.
    await authFetch(
      `api/displays/${encodeURIComponent(id)}/render?force=${force}&wait=false`,
      { method: 'POST' }
    );
    pending.set(id, { at: Date.now(), seen: false });
  });
}

function setSchedule(id, enabled, button) {
  return act(button, () => authFetch(`api/displays/${encodeURIComponent(id)}/schedule`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: enabled }),
  }));
}

function setEnabled(id, enabled, button) {
  const display = state.find((d) => d.id === id);
  if (!display) return Promise.resolve();
  // A PUT replaces the whole configuration, so it goes back the way it came:
  // `config` is the display as the store writes it (`dump_display`,
  // `src/maverick/store.py`), with this one key changed.
  const body = Object.assign({}, display.config, { enabled: enabled });
  return act(button, () => authFetch(`api/displays/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

// --------------------------------------------------------------- the cards --

// Static markup only: every value below is written with textContent or
// setAttribute, so nothing a dashboard name or a lint message contains can
// become markup.
const CARD = `
<h2><span class="card-name"></span><span class="pill"></span></h2>
<div class="meta">
  <code class="card-id"></code> &middot; <span class="card-panel"></span><br>
  <span class="card-geometry"></span><br>
  <span class="muted card-dashboard"></span><br>
  via <code class="card-transport"></code> <span class="card-schedule"></span>
</div>
<img class="shot" alt="" loading="lazy" hidden>
<div class="shot shot-empty" hidden>no frame yet</div>
<div class="meta card-timing">
  <span class="card-next"></span> <span class="card-duration"></span>
</div>
<div class="row">
  <button type="button" class="act-render">Refresh</button>
  <button type="button" class="act-full">Full refresh</button>
  <button type="button" class="act-schedule"></button>
  <button type="button" class="act-enable" hidden>Enable</button>
  <a class="esphome-link">ESPHome config</a>
</div>
<div class="meta err card-error" hidden></div>
<ul class="issues"></ul>`;

function cardFor(id) {
  const existing = cards.get(id);
  if (existing) return existing;
  const card = document.createElement('section');
  card.className = 'card';
  card.innerHTML = CARD;
  const button = (selector) => card.querySelector(selector);
  button('.act-render').addEventListener('click', (e) => requestRender(id, false, e.currentTarget));
  button('.act-full').addEventListener('click', (e) => requestRender(id, true, e.currentTarget));
  button('.act-schedule').addEventListener('click', (e) => {
    const display = state.find((d) => d.id === id);
    setSchedule(id, !(display && display.schedule.enabled), e.currentTarget);
  });
  button('.act-enable').addEventListener('click', (e) => setEnabled(id, true, e.currentTarget));
  cards.set(id, card);
  return card;
}

function paintAll() {
  const main = document.getElementById('displays');
  if (!main) return;
  for (const display of state) {
    const card = cardFor(display.id);
    paint(card, display);
  }
  for (const [id, card] of cards) {
    if (!state.some((display) => display.id === id)) {
      card.remove();
      cards.delete(id);
      pending.delete(id);
    }
  }
  // Appending moves a node, which would blur whatever has focus inside it, so
  // the order is only re-applied when the displays themselves changed.
  const ids = state.map((display) => display.id).join('\n');
  if (ids !== order) {
    order = ids;
    for (const display of state) main.appendChild(cards.get(display.id));
  }
  emptyState(main);
}

function paint(card, display) {
  const rendering = isRendering(display);
  const [pillClass, pillText] = status(display, rendering);

  text(card, '.card-name', display.name);
  text(card, '.card-id', display.id);
  text(card, '.card-panel', display.panel_name);
  text(card, '.card-geometry', `${display.width}×${display.height} · ` +
    `${display.color_scheme} · ${display.dpi} dpi · rot ${display.rotation}°`);
  text(card, '.card-dashboard', display.dashboard);
  text(card, '.card-transport', display.transport);
  text(card, '.card-schedule', scheduleSummary(display.schedule));

  const pill = card.querySelector('.pill');
  pill.className = 'pill ' + pillClass;
  text(card, '.pill', pillText);
  card.classList.toggle('is-disabled', !display.enabled);

  preview(card, display);
  text(card, '.card-next', nextRun(display));
  attribute(card, '.card-next', 'title', display.next_run_at ? absolute(display.next_run_at) : '');
  text(card, '.card-duration', duration(display));
  attribute(card, '.card-duration', 'title', display.last_render_s
    ? `${display.last_render_s.toFixed(2)} s of it in the browser`
    : '');

  const render = card.querySelector('.act-render');
  setText(render, rendering ? 'Rendering…' : 'Refresh');
  setBusy(render, rendering);
  setBusy(card.querySelector('.act-full'), rendering);

  const schedule = card.querySelector('.act-schedule');
  // A disabled display is not on any schedule to pause; Enable is the only
  // thing to do with it.
  schedule.hidden = !display.enabled;
  setText(schedule, display.schedule.enabled ? 'Pause' : 'Resume');
  schedule.title = display.schedule.enabled
    ? 'Stop rendering this display on its schedule'
    : 'Render this display on its schedule again';
  card.querySelector('.act-enable').hidden = display.enabled;

  attribute(card, '.esphome-link', 'href',
    withToken(`api/displays/${encodeURIComponent(display.id)}/esphome.yaml`));

  const error = card.querySelector('.card-error');
  // 300 characters: a Playwright failure runs to pages, and the whole of it
  // is in the log and in `state.last_error` either way.
  const message = (display.state && display.state.last_error) || '';
  error.hidden = !message;
  text(card, '.card-error', message.slice(0, 300));

  issues(card, display.lint ? display.lint.issues : []);
}

/** Whether this display is rendering, or was asked to and has not started yet. */
function isRendering(display) {
  const asked = pending.get(display.id);
  if (asked) {
    if (display.rendering) asked.seen = true;
    else if (asked.seen || Date.now() - asked.at > PENDING_GRACE_MS) pending.delete(display.id);
  }
  return Boolean(display.rendering) || pending.has(display.id);
}

/** The one thing the pill says, most urgent first. */
function status(display, rendering) {
  if (!display.enabled) return ['muted', 'disabled'];
  if (rendering) return ['busy', 'rendering'];
  if (display.state && display.state.consecutive_failures) return ['err', 'error'];
  if (!display.schedule.enabled) return ['muted', 'paused'];
  const found = display.lint ? display.lint.issues : [];
  if (found.some((issue) => issue.severity === 'error')) return ['err', display.lint.summary];
  if (found.some((issue) => issue.severity === 'warning')) return ['warn', display.lint.summary];
  // Not the stored frame: only a pull transport puts one there
  // (`src/maverick/transports/pull.py`), and a push display renders all the
  // same.
  if (display.state && display.state.render_count) return ['ok', 'ok'];
  return ['', 'not rendered'];
}

function preview(card, display) {
  const image = card.querySelector('img.shot');
  const placeholder = card.querySelector('.shot-empty');
  if (!display.checksum) {
    image.hidden = true;
    placeholder.hidden = false;
    return;
  }
  // The preview URL is stable but what it serves is not, so the checksum goes
  // in the query: the browser re-fetches when the frame changes, and only
  // then. setAttribute rather than .src, which would resolve to an absolute
  // URL and lose the ingress prefix.
  if (image.dataset.checksum !== display.checksum) {
    image.dataset.checksum = display.checksum;
    image.setAttribute('src', withToken(
      `api/displays/${encodeURIComponent(display.id)}/preview.png` +
      `?c=${encodeURIComponent(display.checksum)}`
    ));
  }
  image.alt = `current frame for ${display.name}`;
  image.hidden = false;
  placeholder.hidden = true;
}

function issues(card, found) {
  const list = card.querySelector('.issues');
  const signature = JSON.stringify(found);
  if (list.dataset.signature === signature) return;
  list.dataset.signature = signature;
  list.replaceChildren(...found.map((issue) => {
    const item = document.createElement('li');
    const severity = document.createElement('span');
    severity.className = severityClass(issue.severity);
    severity.textContent = issue.severity;
    item.append(severity, ' ' + issue.message);
    if (issue.hint) {
      const hint = document.createElement('div');
      hint.className = 'hint';
      hint.textContent = issue.hint;
      item.append(hint);
    }
    return item;
  }));
}

function severityClass(severity) {
  return { error: 'err', warning: 'warn', info: 'muted' }[severity] || '';
}

const DOCS_URL =
  'https://github.com/ambient-home-systems/maverick-eink-dashboard#configuration';

function emptyState(main) {
  let card = document.getElementById('no-displays');
  if (state.length) {
    if (card) card.remove();
    return;
  }
  if (card) return;
  card = document.createElement('section');
  card.className = 'card';
  card.id = 'no-displays';
  card.innerHTML = `<h2>No displays configured</h2>
<div class="meta">Add a <code>displays:</code> entry to your config and restart.
See the <a class="docs-link">docs</a>.</div>`;
  card.querySelector('.docs-link').href = DOCS_URL;
  main.appendChild(card);
}

// ------------------------------------------------------------- formatting --

const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
const UNITS = [['day', 86400], ['hour', 3600], ['minute', 60], ['second', 1]];

/** "in 4 minutes", "2 minutes ago" — with the absolute time in a title. */
function relative(iso) {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return '';
  const delta = (at - Date.now()) / 1000;
  for (const [unit, size] of UNITS) {
    if (Math.abs(delta) >= size || unit === 'second') {
      return RELATIVE.format(Math.round(delta / size), unit);
    }
  }
  return '';
}

function absolute(iso) {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? '' : at.toLocaleString();
}

function nextRun(display) {
  if (!display.enabled) return 'not scheduled while disabled';
  if (!display.schedule.enabled) return 'schedule paused';
  if (!display.next_run_at) return 'no scheduled render';
  return 'next render ' + relative(display.next_run_at);
}

function duration(display) {
  if (!display.last_total_s) return '';
  return `· last took ${display.last_total_s.toFixed(2)} s`;
}

function scheduleSummary(schedule) {
  const bits = [];
  if (schedule.every) bits.push('every ' + schedule.every);
  if (schedule.cron) bits.push('cron ' + schedule.cron);
  if (schedule.quiet_hours) bits.push('quiet ' + schedule.quiet_hours);
  if (schedule.on_change && schedule.on_change.length) {
    bits.push(schedule.on_change.length + ' triggers');
  }
  return bits.length ? '· ' + bits.join(', ') : '';
}

function text(root, selector, value) {
  setText(root.querySelector(selector), value);
}

// Only when it changed: rewriting textContent replaces the text node, and a
// poll every five seconds should not be able to drop a selection.
function setText(node, value) {
  if (node && node.textContent !== value) node.textContent = value;
}

function attribute(root, selector, name, value) {
  const node = root.querySelector(selector);
  if (!node || node.getAttribute(name) === value) return;
  if (value) node.setAttribute(name, value);
  else node.removeAttribute(name);
}

function notice(message) {
  const box = document.getElementById('notice');
  if (!box) return;
  box.textContent = message;
  box.hidden = !message;
}

function report(error) {
  notice(error && error.message ? error.message : String(error));
}

// ---------------------------------------------------------------- start-up --

for (const form of document.querySelectorAll('form.tokenbox')) {
  form.addEventListener('submit', applyToken);
}

const main = document.getElementById('displays');
if (main) {
  // A token arriving in the query is remembered for the rest of the tab, so
  // the preview images and the ESPHome link — neither of which can send a
  // header — get it appended to their URLs.
  const supplied = new URLSearchParams(location.search).get('token');
  if (supplied) storeToken(supplied);

  const initial = document.getElementById('initial-displays');
  if (initial) {
    try { state = JSON.parse(initial.textContent); } catch (e) { state = []; }
  }
  paintAll();
  schedulePoll();

  // A background tab is showing nobody anything, so it asks for nothing; the
  // first thing a tab does on coming back is find out what it missed.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      clearTimeout(timer);
      timer = null;
    } else {
      pollAgain();
    }
  });
}
