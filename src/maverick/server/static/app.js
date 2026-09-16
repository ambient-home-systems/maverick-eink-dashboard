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
  <button type="button" class="act-edit">Edit</button>
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
  button('.act-edit').addEventListener('click', (e) => openEditor(id, e.currentTarget));
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
<div class="meta">Nothing is set up to render yet.</div>
<div class="row"><button type="button" class="add-btn">Add display</button></div>`;
  card.querySelector('.add-btn').addEventListener('click', (e) => openAddDialog(e.currentTarget));
  main.appendChild(card);
}

// ----------------------------------------------------- the add-display form --

// The dialog's markup is server-rendered (`src/maverick/server/ui.py`) since
// its fields are fixed, unlike the per-display editor below, which builds
// every control from the schema. What this module owns is everything the markup cannot know without
// a fetch: the panel and transport lists, every field's help text (each
// node's `data-help` names a path into `GET /api/schema/display`, so the copy
// here and the description in `src/maverick/config.py` cannot drift apart),
// and turning a 422 or 409 from `POST /api/displays` into a message under the
// field it is about.

/** Cached once per page load: the three endpoints a form is built from, shared
 *  by this dialog and the per-display editor below. */
let formData = null;
/** The element focus should return to when the dialog closes. */
let addDialogOpener = null;
/** Set just before closing on a successful add, so `close` offers it instead. */
let addDialogJustAdded = '';
/** Whether the user has typed in the id field directly, so name no longer drives it. */
let addIdEdited = false;

function slugify(value) {
  return (value || '')
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9_-]/g, '')
    .replace(/^[^a-z0-9]+/, '');
}

async function openAddDialog(opener) {
  const dialog = document.getElementById('add-dialog');
  if (!dialog) return;
  addDialogOpener = opener || document.activeElement;
  resetAddForm();
  dialog.showModal();
  document.getElementById('add-name').focus();
  await ensureAddDialogData();
  populateDashboardList(document.getElementById('add-dashboard-list'));
}

function resetAddForm() {
  const form = document.getElementById('add-form');
  if (form) form.reset();
  addIdEdited = false;
  dialogError('add-dialog-error', '');
  dialogError('add-network-error', '');
  for (const el of document.querySelectorAll('#add-dialog .field-error')) {
    el.textContent = '';
    el.hidden = true;
  }
  // `form.reset()` puts the panel and transport <select>s back to their first
  // option, but does not know to refresh what depends on the *value* of
  // either — the panel notes, the advanced placeholders, the transport's own
  // option fields — so a second open of a dialog left mid-edit would show
  // last time's transport fields under this time's selection.
  if (formData) {
    onPanelChange();
    onTransportChange();
  } else {
    updatePanelNotes(null);
  }
}

async function ensureFormData() {
  if (formData) return formData;
  const [panelsRes, transportsRes, schemaRes] = await Promise.all([
    authFetch('api/panels'), authFetch('api/transports'), authFetch('api/schema/display'),
  ]);
  formData = {
    panels: await panelsRes.json(),
    transports: await transportsRes.json(),
    schema: await schemaRes.json(),
  };
  return formData;
}

/** Cached once per page load: `GET /api/ha/dashboards`, or `[]` when it is
 *  unavailable (no Home Assistant connection, or an old server without the
 *  route). Either way the Dashboard field stays a plain text input — this
 *  only ever adds suggestions to it, never replaces it. */
let dashboardsCache = null;

async function ensureDashboards() {
  if (dashboardsCache) return dashboardsCache;
  try {
    const response = await authFetch('api/ha/dashboards');
    dashboardsCache = await response.json();
  } catch (error) {
    dashboardsCache = [];
  }
  return dashboardsCache;
}

/** Fills a `<datalist>` with one option per view — the paths a Dashboard
 *  field can actually be pointed at — labelled with the dashboard and view
 *  titles so a user picks "Home — Kitchen" rather than "/lovelace/1". Fails
 *  quietly: an empty datalist leaves the field exactly the plain text input
 *  it already is. */
async function populateDashboardList(datalist) {
  if (!datalist) return;
  const dashboards = await ensureDashboards();
  datalist.replaceChildren();
  for (const dashboard of dashboards) {
    for (const view of dashboard.views || []) {
      const option = document.createElement('option');
      option.value = view.path;
      option.label = `${dashboard.title} — ${view.title}`;
      datalist.appendChild(option);
    }
  }
}

async function ensureAddDialogData() {
  const submit = document.getElementById('add-submit');
  if (formData) return;
  setBusy(submit, true);
  try {
    populateAddDialog(await ensureFormData());
  } catch (error) {
    if (!error.unauthorised) dialogError('add-dialog-error', 'Could not load the form: ' + error.message);
  } finally {
    setBusy(submit, false);
  }
}

function populateAddDialog(data) {
  fillHelpTexts(data.schema);
  populatePanelSelect(document.getElementById('add-panel'), data.panels);
  populateEnumSelect(
    document.getElementById('add-color-scheme'), enumValues(data.schema, 'ColorScheme'),
    'panel default'
  );
  populateEnumSelect(
    document.getElementById('add-frame-format'), enumValues(data.schema, 'FrameFormat'),
    'panel default'
  );
  populateTransportSelect(document.getElementById('add-transport'), data.transports);
  onPanelChange();
  onTransportChange();
}

/** `data-help="dashboard"` or `data-help="schedule.every"` -> a schema description. */
function fillHelpTexts(schema) {
  for (const el of document.querySelectorAll('#add-dialog [data-help]')) {
    const path = el.dataset.help.split('.');
    let node = path.length === 2 ? schema.$defs.ScheduleConfig.properties[path[1]] : schema.properties[path[0]];
    el.textContent = (node && node.description) || '';
  }
}

function populatePanelSelect(select, panels) {
  const groups = new Map();
  for (const p of panels) {
    const label = p.vendor ? p.vendor[0].toUpperCase() + p.vendor.slice(1) : 'Other';
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(p);
  }
  select.replaceChildren();
  for (const label of [...groups.keys()].sort()) {
    const group = document.createElement('optgroup');
    group.label = label;
    for (const p of groups.get(label).sort((a, b) => a.name.localeCompare(b.name))) {
      const opt = document.createElement('option');
      opt.value = p.id;
      opt.textContent = `${p.name} — ${p.width}×${p.height} · ${p.color_scheme} · ${p.dpi} dpi`;
      group.appendChild(opt);
    }
    select.appendChild(group);
  }
}

function populateTransportSelect(select, transports) {
  select.replaceChildren();
  for (const t of transports) {
    const opt = document.createElement('option');
    opt.value = t.name;
    opt.textContent = t.name;
    opt.title = t.description;
    select.appendChild(opt);
  }
}

/** An enum <select>. `emptyLabel` adds a `""` option meaning "let the panel
 *  decide", whose label picks up the panel's own value; null leaves it out,
 *  for an enum the model always has a value for. */
function populateEnumSelect(select, values, emptyLabel) {
  select.replaceChildren();
  if (emptyLabel !== null) {
    const unset = document.createElement('option');
    unset.value = '';
    unset.textContent = emptyLabel;
    select.appendChild(unset);
  }
  for (const value of values) {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = value;
    select.appendChild(opt);
  }
}

function enumValues(schema, defName) {
  return ((schema.$defs || {})[defName] || {}).enum || [];
}

function currentPanel() {
  if (!formData) return null;
  const id = document.getElementById('add-panel').value;
  return formData.panels.find((p) => p.id === id) || null;
}

function onPanelChange() {
  const panel = currentPanel();
  updatePanelNotes(panel);
  updateAdvancedPlaceholders(panel);
}

function updatePanelNotes(panel) {
  const notes = document.getElementById('add-panel-notes');
  notes.textContent = (panel && panel.notes) || '';
  notes.hidden = !notes.textContent;
}

function updateAdvancedPlaceholders(panel) {
  if (!panel) return;
  document.getElementById('add-width').placeholder = String(panel.width);
  document.getElementById('add-height').placeholder = String(panel.height);
  document.getElementById('add-dpi').placeholder = String(panel.dpi);
  defaultOptionLabel('add-color-scheme', `panel default (${panel.color_scheme})`);
  defaultOptionLabel('add-rotation', `panel default (${panel.rotation}°)`);
  defaultOptionLabel('add-frame-format', panel.frame_format
    ? `panel default (${panel.frame_format})`
    : 'panel default (from transport)');
}

function defaultOptionLabel(selectId, label) {
  const option = document.querySelector(`#${selectId} option[value=""]`);
  if (option) option.textContent = label;
}

function onTransportChange() {
  const container = document.getElementById('add-transport-options');
  const help = document.getElementById('add-transport-help');
  container.replaceChildren();
  if (!formData) return;
  const type = document.getElementById('add-transport').value;
  const info = formData.schema.transports[type];
  help.textContent = (info && info.description) || '';
  if (!info) return;
  if (type === 'mqtt') {
    const note = document.createElement('div');
    note.className = 'help warn';
    note.textContent = 'Needs MQTT enabled globally: mqtt.enabled: true, or the ' +
      'Mosquitto broker app under the Supervisor.';
    container.appendChild(note);
  }
  for (const [key, description] of Object.entries(info.options)) {
    const field = document.createElement('div');
    field.className = 'field';
    const id = `add-opt-${key}`;
    field.innerHTML = `<label for="${id}"></label><input type="text" autocomplete="off">` +
      `<div class="help"></div>`;
    const label = field.querySelector('label');
    label.setAttribute('for', id);
    label.textContent = key;
    const input = field.querySelector('input');
    input.id = id;
    input.dataset.transportKey = key;
    field.querySelector('.help').textContent = description;
    container.appendChild(field);
  }
}

function dialogError(id, message) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = message;
  el.hidden = !message;
}

function fieldError(name, message) {
  const el = document.querySelector(`#add-dialog [data-error="${name}"]`);
  if (!el) {
    dialogError('add-dialog-error', message);
    return;
  }
  el.textContent = message;
  el.hidden = !message;
}

function buildAddBody() {
  const value = (id) => document.getElementById(id).value.trim();
  const schedule = {};
  if (value('add-every')) schedule.every = value('add-every');
  if (value('add-cron')) schedule.cron = value('add-cron');
  if (value('add-quiet-hours')) schedule.quiet_hours = value('add-quiet-hours');
  const onChange = value('add-on-change').split(',').map((s) => s.trim()).filter(Boolean);
  if (onChange.length) schedule.on_change = onChange;

  const transport = { type: value('add-transport') };
  for (const input of document.querySelectorAll('#add-transport-options [data-transport-key]')) {
    if (input.value.trim()) transport[input.dataset.transportKey] = input.value.trim();
  }

  const body = {
    id: value('add-id'),
    name: value('add-name'),
    panel: value('add-panel'),
    dashboard: value('add-dashboard') || '/lovelace/0',
    enabled: document.getElementById('add-enabled').checked,
    schedule,
    transport,
  };
  if (value('add-width')) body.width = Number(value('add-width'));
  if (value('add-height')) body.height = Number(value('add-height'));
  if (value('add-color-scheme')) body.color_scheme = value('add-color-scheme');
  if (value('add-dpi')) body.dpi = Number(value('add-dpi'));
  if (value('add-rotation')) body.rotation = Number(value('add-rotation'));
  if (value('add-frame-format')) body.frame_format = value('add-frame-format');
  return body;
}

/**
 * Like `authFetch`, but keeps the structured 422/409 body instead of
 * flattening it: a 422 from pydantic is a list of `{loc, msg}` naming the
 * field that failed, which both the Add display dialog and the per-display
 * editor put back under the field rather than in a banner.
 */
async function sendJSON(url, method, body, fallback) {
  const headers = Object.assign(
    body === null ? {} : { 'Content-Type': 'application/json' }, authHeaders()
  );
  let response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === null ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    const error = new Error('Could not reach Maverick. Check the connection and try again.');
    error.network = true;
    throw error;
  }
  if (response.status === 401) {
    askForToken();
    const error = new Error('Maverick needs its API token.');
    error.unauthorised = true;
    throw error;
  }
  // 204 is what DELETE answers, and it has no body to parse.
  if (response.ok) return response.status === 204 ? null : response.json();
  let detail = null;
  try { detail = (await response.json()).detail; } catch (e) { /* no body */ }
  const error = new Error(
    typeof detail === 'string' && detail
      ? detail
      : (fallback || `${response.status} ${response.statusText}`)
  );
  error.status = response.status;
  error.fields = Array.isArray(detail) ? detail : null;
  throw error;
}

async function submitAddDisplay(event) {
  event.preventDefault();
  const submit = document.getElementById('add-submit');
  dialogError('add-dialog-error', '');
  dialogError('add-network-error', '');
  for (const el of document.querySelectorAll('#add-dialog .field-error')) {
    el.textContent = '';
    el.hidden = true;
  }
  setBusy(submit, true);
  try {
    const created = await sendJSON(
      'api/displays', 'POST', buildAddBody(), 'The display could not be added.'
    );
    // Poll before closing, not after: the `close` event (queued, not
    // synchronous) is what moves focus onward, and it has to have a finished
    // card to focus rather than racing it.
    await poll();
    addDialogJustAdded = created.id;
    document.getElementById('add-dialog').close();
  } catch (error) {
    if (error.unauthorised) {
      // The token field is already showing; the dialog stays open with
      // everything the user typed so far still in it.
    } else if (error.network) {
      dialogError('add-network-error', error.message);
    } else if (error.status === 409) {
      fieldError('id', error.message);
    } else if (error.fields) {
      applyFieldErrors(error.fields);
    } else {
      dialogError('add-dialog-error', error.message);
    }
  } finally {
    setBusy(submit, false);
  }
}

/** FastAPI's 422 body: a list of `{loc: ["body", "field", ...], msg}`. */
function applyFieldErrors(fields) {
  for (const item of fields) {
    const loc = item.loc || [];
    const name = loc.length > 1 ? String(loc[1]) : '';
    const message = String(item.msg || '').replace(/^Value error,\s*/, '');
    fieldError(name, message);
  }
}

/** Draws the user's eye to the new card's own render action rather than adding a
 * second button that would do the same thing every other card already offers. */
function offerRenderNow(id) {
  const card = cards.get(id);
  if (!card) return;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  card.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'center' });
  const button = card.querySelector('.act-render');
  if (button) button.focus();
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

const addDialog = document.getElementById('add-dialog');
if (addDialog) {
  document.getElementById('add-display-btn')?.addEventListener(
    'click', (e) => openAddDialog(e.currentTarget)
  );
  document.getElementById('add-cancel').addEventListener('click', () => addDialog.close());
  document.getElementById('add-form').addEventListener('submit', submitAddDisplay);
  document.getElementById('add-panel').addEventListener('change', onPanelChange);
  document.getElementById('add-transport').addEventListener('change', onTransportChange);
  document.getElementById('add-name').addEventListener('input', (e) => {
    if (!addIdEdited) document.getElementById('add-id').value = slugify(e.target.value);
  });
  document.getElementById('add-id').addEventListener('input', () => { addIdEdited = true; });
  // A native <dialog> already returns focus to whatever was focused before
  // `showModal()`, but only when that element is still in the document — the
  // empty-state's Add display button is recreated on every poll, so it can be
  // gone by the time this fires. A successful add offers the new card's own
  // render action instead of returning focus to the opener.
  addDialog.addEventListener('close', () => {
    const justAdded = addDialogJustAdded;
    addDialogJustAdded = '';
    if (justAdded) {
      offerRenderNow(justAdded);
    } else if (addDialogOpener && document.body.contains(addDialogOpener)) {
      addDialogOpener.focus();
    }
    addDialogOpener = null;
  });
}

// ------------------------------------------------------ the display editor --

/* The tuning loop, in the page: change a threshold, preview it, save it.
 *
 * Every control below is drawn from `GET /api/schema/display`, which is
 * `DisplayConfig.model_json_schema()` — so a field added in
 * `src/maverick/config.py` appears here with its own `Field(description=...)`
 * as its help text and nothing in this file to change. The schema decides the
 * control: a boolean is a switch, a bounded number is a number input carrying
 * those bounds, an enum is a select, `list[str]` is a comma-separated box, and
 * the two shapes that deserve better than a text field — the measured inks and
 * the extra stylesheet — are a colour table and a textarea.
 *
 * Two rules the models impose (`src/maverick/config.py`). Every section but the
 * transport is `extra="forbid"`, so each one sends known keys only: one stray
 * key fails the whole `PUT` rather than being ignored. `TransportConfig` is the
 * documented exception, `extra="allow"`, and its options are not in the schema
 * at all — they are each transport's own `options_doc`, which
 * `GET /api/schema/display` returns under `transports` — so whatever keys a
 * display already carries there are shown and kept rather than dropped for
 * being undocumented.
 */

/** The nested models, in the order the drawer shows them under Display. */
const SECTION_TITLES = {
  schedule: 'Schedule', theme: 'Theme', image: 'Image', render: 'Render',
  lint: 'Lint', transport: 'Transport', pack: 'Pack', esphome: 'ESPHome',
};

/** Top-level fields that lead the Display section; the rest follow in schema order. */
const DISPLAY_FIRST = ['name', 'dashboard', 'panel', 'enabled'];

/** Fields left empty because the panel profile supplies the value. */
const PANEL_RESOLVED = ['width', 'height', 'color_scheme', 'dpi', 'rotation', 'frame_format'];

/** Every field something else fills in when it is left empty: those six, and
 *  `name`, which an empty one derives from the id (`DisplayConfig._defaults`,
 *  `src/maverick/config.py`). The summary carries what each one resolved to
 *  (`_display_summary`, `src/maverick/server/api.py`), and it becomes the
 *  placeholder — so an empty box says what leaving it empty will mean rather
 *  than looking like something went missing. */
const RESOLVED = ['name', ...PANEL_RESOLVED];

/** A stylesheet is not a one-line input. */
const TEXTAREAS = new Set(['theme.extra_css']);

const EDITOR = `
<div class="drawer-head">
  <div>
    <h2 id="editor-title"></h2>
    <div class="meta"><code class="editor-id"></code> <span class="editor-panel"></span></div>
  </div>
  <span class="pill warn editor-dirty" hidden>unsaved changes</span>
  <button type="button" class="drawer-close" aria-label="Close the editor">&times;</button>
</div>
<p class="dialog-error editor-error" role="alert" hidden></p>
<datalist id="editor-dashboard-list"></datalist>
<div class="drawer-body"></div>
<div class="drawer-actions">
  <div class="editor-confirm" role="alert" hidden>
    <span class="editor-confirm-text"></span>
    <button type="button" class="editor-confirm-yes"></button>
    <button type="button" class="editor-confirm-no">Cancel</button>
  </div>
  <div class="editor-buttons">
    <button type="button" class="act-delete">Delete</button>
    <span class="spacer"></span>
    <button type="button" class="act-close">Close</button>
    <button type="button" class="act-save add-btn">Save</button>
  </div>
</div>`;

const PREVIEW = `
<div class="row">
  <button type="button" class="act-preview">Preview</button>
  <span class="preview-status"></span>
  <span class="spacer"></span>
  <span class="compare-modes" role="group" aria-label="Compare the two frames" hidden>
    <button type="button" class="mode-side" aria-pressed="true">Side by side</button>
    <button type="button" class="mode-overlay" aria-pressed="false">Overlay</button>
  </span>
</div>
<div class="help">Renders this configuration and shows the frame it would
  produce. It saves nothing and sends nothing to the panel — Save is what
  writes the configuration. A render can take as long as
  <code>render.timeout</code>; the rest of the drawer stays usable while it
  runs.</div>
<div class="compare is-side" hidden>
  <figure class="compare-now">
    <img class="shot" alt="" hidden><div class="shot shot-empty">no frame yet</div>
    <figcaption>On the panel now</figcaption>
  </figure>
  <figure class="compare-new">
    <img class="shot" alt="" hidden><div class="shot shot-empty">not previewed yet</div>
    <figcaption>This configuration</figcaption>
  </figure>
</div>
<label class="compare-mix" hidden>Fade to this configuration
  <input type="range" min="0" max="100" value="100"></label>
<ul class="issues preview-issues"></ul>`;

/** The one drawer, built on first use and refilled on every open. */
let editorDialog = null;
let editorId = '';
/** The `GET /api/displays/{id}` payload the open drawer was filled from. */
let editorSummary = null;
/** One entry per generated control: `{path, section, name, spec, read}`. */
let editorFields = [];
/** The transport section's working copy, so a look at another transport and
 *  back does not cost what was typed under this one. */
let editorTransport = {};
/** The keys the stored config has under `transport`, which stay on screen
 *  whichever transport is selected. */
let storedTransportKeys = [];
/** `collectBody()` as the drawer was loaded, for the unsaved-changes guard. */
let editorBaseline = '';
let editorOpener = null;
let editorDeleted = false;
let editorNeedsToken = false;
/** What the confirmation row's accept button does, set by `askFirst`. */
let editorConfirmAction = null;
let previewTicker = null;

// ------------------------------------------------------------ opening it --

async function openEditor(id, opener) {
  const dialog = ensureEditor();
  editorOpener = opener || document.activeElement;
  editorId = id;
  editorSummary = null;
  editorFields = [];
  editorBaseline = '';
  editorDeleted = false;
  editorNeedsToken = false;
  hideConfirm();
  editorError('');
  setText(dialog.querySelector('#editor-title'), 'Loading…');
  setText(dialog.querySelector('.editor-id'), id);
  setText(dialog.querySelector('.editor-panel'), '');
  dialog.querySelector('.drawer-body').replaceChildren();
  if (!dialog.open) dialog.showModal();
  try {
    const [data, summary] = await Promise.all([ensureFormData(), loadDisplay(id)]);
    fillEditor(data, summary);
    populateDashboardList(dialog.querySelector('#editor-dashboard-list'));
  } catch (error) {
    // A modal dialog makes the header's token field inert, so a 401 has to
    // close the drawer to leave the field it just revealed reachable.
    if (error.unauthorised) {
      editorNeedsToken = true;
      closeEditor();
      return;
    }
    editorError('Could not open the editor: ' + error.message);
  }
}

function ensureEditor() {
  if (editorDialog) return editorDialog;
  const dialog = document.createElement('dialog');
  dialog.id = 'editor';
  dialog.className = 'drawer';
  dialog.setAttribute('aria-labelledby', 'editor-title');
  dialog.innerHTML = EDITOR;
  document.body.appendChild(dialog);

  dialog.querySelector('.drawer-close').addEventListener('click', requestClose);
  dialog.querySelector('.act-close').addEventListener('click', requestClose);
  dialog.querySelector('.act-save').addEventListener('click', (e) => saveEditor(e.currentTarget));
  dialog.querySelector('.act-delete').addEventListener('click', askToDelete);
  dialog.querySelector('.editor-confirm-no').addEventListener('click', hideConfirm);
  dialog.querySelector('.editor-confirm-yes').addEventListener('click', () => {
    const action = editorConfirmAction;
    hideConfirm();
    if (action) action();
  });
  // Escape fires `cancel`, which would close the drawer and drop whatever is
  // in it; intercepted so unsaved changes get a question first.
  dialog.addEventListener('cancel', (event) => {
    event.preventDefault();
    requestClose();
  });
  dialog.addEventListener('close', afterClose);
  // Anything typed anywhere in the drawer can change what Save would send, so
  // the guard follows the controls rather than each one announcing itself.
  dialog.addEventListener('input', markDirty);
  dialog.addEventListener('change', markDirty);
  editorDialog = dialog;
  return dialog;
}

async function loadDisplay(id) {
  const response = await authFetch(`api/displays/${encodeURIComponent(id)}`);
  return response.json();
}

function fillEditor(data, summary) {
  editorSummary = summary;
  editorFields = [];
  editorTransport = Object.assign({}, (summary.config && summary.config.transport) || {});
  storedTransportKeys = Object.keys(editorTransport).filter((key) => key !== 'type');
  const schema = data.schema;
  setText(editorDialog.querySelector('#editor-title'), summary.name || summary.id);
  setText(editorDialog.querySelector('.editor-id'), summary.id);
  setText(editorDialog.querySelector('.editor-panel'), '· ' + summary.panel_name);

  const parts = [previewSection(summary)];
  parts.push(sectionNode('', 'Display', displayFields(schema, data, summary), true));
  for (const [key, title] of sectionOrder(schema)) {
    const fields = key === 'transport'
      ? transportFields(schema, data)
      : modelFields(schema, key);
    parts.push(sectionNode(key, title, fields, false, sectionHelp(schema, key)));
  }
  editorDialog.querySelector('.drawer-body').replaceChildren(...parts);
  // Every field is registered and in the document by now, which is what the
  // panel's placeholders and the first dirty check both need.
  onEditorPanelChange();
  editorBaseline = JSON.stringify(collectBody());
  markDirty();
}

/** The nested models the schema has, the ones named above first. */
function sectionOrder(schema) {
  const nested = Object.keys(schema.properties).filter((key) => isSection(schema, key));
  const known = Object.keys(SECTION_TITLES).filter((key) => nested.includes(key));
  // A model added to `DisplayConfig` and not named above still gets a section,
  // titled from the schema, rather than silently going missing from the editor.
  const rest = nested.filter((key) => !(key in SECTION_TITLES));
  return [
    ...known.map((key) => [key, SECTION_TITLES[key]]),
    ...rest.map((key) => [key, deref(schema, schema.properties[key].$ref).title || key]),
  ];
}

function isSection(schema, key) {
  const node = schema.properties[key];
  if (!node || !node.$ref) return false;
  return Boolean((deref(schema, node.$ref) || {}).properties);
}

function deref(schema, ref) {
  const name = String(ref || '').replace('#/$defs/', '');
  return (schema.$defs || {})[name] || {};
}

// ----------------------------------------------------------- the sections --

/** What a nested model says about itself.
 *
 * Pydantic moves a field's description onto the definition it refers to when
 * the field has nothing else to say, so `theme` carries none and
 * `$defs.ThemeConfig` carries "Overrides for the injected e-ink stylesheet."
 * The field's own wins where there is one, since it describes this use of the
 * model rather than the model.
 */
function sectionHelp(schema, key) {
  const node = schema.properties[key] || {};
  return node.description || deref(schema, node.$ref).description || '';
}

function sectionNode(key, title, fields, open, helpText) {
  const section = document.createElement('details');
  section.className = 'section';
  section.dataset.section = key;
  if (open) section.open = true;
  const summary = document.createElement('summary');
  summary.textContent = title;
  const body = document.createElement('div');
  body.className = 'section-body';
  const help = document.createElement('div');
  help.className = 'help';
  help.textContent = helpText || '';
  const error = document.createElement('div');
  error.className = 'field-error section-error';
  body.append(help, error, ...fields);
  section.append(summary, body);
  return section;
}

function displayFields(schema, data, summary) {
  const names = Object.keys(schema.properties).filter(
    // `id` is the path a PUT goes to and the key every stored frame is under;
    // it is shown in the header rather than offered as something to change.
    (key) => key !== 'id' && !isSection(schema, key)
  );
  const ordered = [
    ...DISPLAY_FIRST.filter((key) => names.includes(key)),
    ...names.filter((key) => !DISPLAY_FIRST.includes(key)),
  ];
  return ordered.map((name) => {
    if (name === 'panel') return panelField(schema, data, summary);
    return fieldNode(schema, '', name, schema.properties[name], resolvedFor(summary, name));
  });
}

function modelFields(schema, key) {
  const def = deref(schema, schema.properties[key].$ref);
  return Object.entries(def.properties || {}).map(
    ([name, node]) => fieldNode(schema, key, name, node, '')
  );
}

/** What an empty box for this field resolves to, or '' for a field nothing
 *  fills in. */
function resolvedFor(summary, name) {
  if (!RESOLVED.includes(name)) return '';
  const value = summary[name];
  return value === null || value === undefined ? '' : String(value);
}

// ------------------------------------------------------------ the controls --

/** What the schema says a field is, in the terms a control needs. */
function specFor(schema, node) {
  const spec = { kind: 'text', nullable: false, values: [], step: 'any' };
  let branches = [node];
  if (node.anyOf) {
    spec.nullable = node.anyOf.some((branch) => branch.type === 'null');
    branches = node.anyOf.filter((branch) => branch.type !== 'null');
  }
  const enumRef = branches.find((branch) => branch.$ref);
  if (enumRef) {
    spec.kind = 'enum';
    spec.values = deref(schema, enumRef.$ref).enum || [];
    return spec;
  }
  // `str | float`: a duration, whose whole point is that it can be written
  // "45s" (`parse_duration`, `src/maverick/config.py`). A text box takes both.
  if (branches.length > 1) return spec;
  const branch = branches[0] || {};
  if (branch.type === 'boolean') {
    spec.kind = 'boolean';
  } else if (branch.type === 'integer' || branch.type === 'number') {
    spec.kind = 'number';
    spec.step = branch.type === 'integer' ? '1' : 'any';
    // An exclusive bound is a bound the browser cannot express, so it is given
    // as an inclusive one: the model has the last word either way, and says so
    // as a 422 this drawer puts back under the field.
    spec.min = pick(branch.minimum, branch.exclusiveMinimum);
    spec.max = pick(branch.maximum, branch.exclusiveMaximum);
  } else if (branch.type === 'array') {
    spec.kind = 'strings';
  } else if (branch.type === 'object' && (branch.additionalProperties || {}).type === 'array') {
    spec.kind = 'palette';
  }
  return spec;
}

function pick(first, second) {
  return first === undefined ? second : first;
}

/** One labelled control, registered in `editorFields` so `collectBody` can read it. */
function fieldNode(schema, section, name, node, placeholder) {
  const path = section ? `${section}.${name}` : name;
  const spec = specFor(schema, node);
  const field = document.createElement('div');
  field.className = 'field';
  field.dataset.path = path;

  const id = 'editor-' + path.replace(/\./g, '-');
  const current = valueOf(section, name, node);
  const built = spec.kind === 'palette'
    ? paletteControl(id, current)
    : plainControl(spec, id, current, placeholder, TEXTAREAS.has(path));
  // Same picker as the Add dialog's Dashboard field, fed by the datalist in
  // the drawer's own markup (`EDITOR` above) rather than the add dialog's.
  if (path === 'dashboard') built.control.setAttribute('list', 'editor-dashboard-list');

  const help = document.createElement('div');
  help.className = 'help';
  help.textContent = node.description || '';
  const error = document.createElement('div');
  error.className = 'field-error';

  if (spec.kind === 'boolean') {
    // A switch reads as one line: the control, then what it does. The label
    // wraps the checkbox rather than pointing at it, which is what makes the
    // whole line a hit target.
    field.classList.add('checkbox');
    const wrap = document.createElement('label');
    wrap.append(built.control, ' ' + name);
    field.append(wrap, help, error);
  } else {
    const label = document.createElement('label');
    label.setAttribute('for', id);
    label.textContent = name;
    field.append(label, built.control, help, error);
  }
  editorFields.push({ path, section, name, spec, read: built.read, control: built.control });
  return field;
}

function plainControl(spec, id, current, placeholder, textarea) {
  let control;
  if (spec.kind === 'boolean') {
    control = document.createElement('input');
    control.type = 'checkbox';
    control.checked = Boolean(current);
  } else if (spec.kind === 'enum') {
    control = document.createElement('select');
    populateEnumSelect(control, spec.values, spec.nullable
      ? (placeholder ? `panel default (${placeholder})` : 'panel default')
      : null);
    control.value = current === null || current === undefined ? '' : String(current);
  } else if (spec.kind === 'number') {
    control = document.createElement('input');
    control.type = 'number';
    control.step = spec.step;
    if (spec.min !== undefined) control.min = String(spec.min);
    if (spec.max !== undefined) control.max = String(spec.max);
    control.value = current === null || current === undefined ? '' : String(current);
  } else if (textarea) {
    control = document.createElement('textarea');
    control.rows = 5;
    control.value = current === null || current === undefined ? '' : String(current);
  } else {
    control = document.createElement('input');
    control.type = 'text';
    control.autocomplete = 'off';
    control.value = listOrText(spec, current);
  }
  control.id = id;
  // An enum says what an empty selection means in the option's own label
  // instead, since a <select> has no placeholder.
  if (spec.kind === 'strings') control.placeholder = placeholder || 'a, b, c';
  else if (placeholder && spec.kind !== 'enum') control.placeholder = placeholder;
  return { control, read: () => readControl(spec, control, textarea) };
}

function listOrText(spec, current) {
  if (current === null || current === undefined) return '';
  if (spec.kind === 'strings') return Array.isArray(current) ? current.join(', ') : String(current);
  return String(current);
}

/* An empty control means "leave this to its default", which is the same thing
 * the key being absent from the file means — so it is left out of the body
 * rather than sent as an empty string. That is what lets Preview and Save send
 * only the keys a display actually sets, and what lets a geometry override be
 * cleared back to the panel's own value. */
function readControl(spec, control, textarea) {
  if (spec.kind === 'boolean') return control.checked;
  const raw = textarea ? control.value : control.value.trim();
  if (raw === '') return undefined;
  if (spec.kind === 'number') {
    const value = Number(raw);
    return Number.isNaN(value) ? raw : value;
  }
  if (spec.kind === 'strings') {
    const items = raw.split(',').map((item) => item.trim()).filter(Boolean);
    return items.length ? items : undefined;
  }
  return raw;
}

/** `image.palette_overrides`: measured inks, so each row shows the colour it names. */
function paletteControl(id, current) {
  const box = document.createElement('div');
  box.className = 'palette';
  box.id = id;
  const rows = document.createElement('div');
  rows.className = 'palette-rows';
  const add = document.createElement('button');
  add.type = 'button';
  add.className = 'palette-add';
  add.textContent = 'Add an ink';
  add.addEventListener('click', () => {
    rows.appendChild(paletteRow('', [0, 0, 0]));
    markDirty();
  });
  for (const [name, rgb] of Object.entries(current || {})) rows.appendChild(paletteRow(name, rgb));
  box.append(rows, add);
  return {
    control: box,
    read: () => {
      const value = {};
      for (const row of rows.querySelectorAll('.palette-row')) {
        const inputs = row.querySelectorAll('input');
        const name = inputs[0].value.trim();
        if (!name) continue;
        value[name] = [1, 2, 3].map((i) => Number(inputs[i].value || 0));
      }
      return Object.keys(value).length ? value : undefined;
    },
  };
}

function paletteRow(name, rgb) {
  const row = document.createElement('div');
  row.className = 'palette-row';
  const swatch = document.createElement('span');
  swatch.className = 'swatch';
  const fields = [];
  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.className = 'palette-name';
  nameInput.value = name;
  nameInput.setAttribute('aria-label', 'palette name');
  fields.push(nameInput);
  for (let i = 0; i < 3; i += 1) {
    const channel = document.createElement('input');
    channel.type = 'number';
    channel.min = '0';
    channel.max = '255';
    channel.step = '1';
    channel.className = 'palette-channel';
    channel.value = String((rgb || [])[i] ?? 0);
    channel.setAttribute('aria-label', ['red', 'green', 'blue'][i]);
    fields.push(channel);
  }
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'palette-remove';
  remove.setAttribute('aria-label', 'remove this ink');
  remove.textContent = '×';
  remove.addEventListener('click', () => {
    row.remove();
    markDirty();
  });
  const paint = () => {
    const [r, g, b] = fields.slice(1).map((input) => clamp(Number(input.value || 0)));
    swatch.style.background = `rgb(${r},${g},${b})`;
  };
  for (const input of fields) input.addEventListener('input', paint);
  paint();
  row.append(swatch, ...fields, remove);
  return row;
}

function clamp(value) {
  return Math.max(0, Math.min(255, Number.isFinite(value) ? Math.round(value) : 0));
}

/** The panel select, grouped by vendor like the Add display form's. */
function panelField(schema, data, summary) {
  const node = schema.properties.panel;
  const field = document.createElement('div');
  field.className = 'field';
  field.dataset.path = 'panel';
  const label = document.createElement('label');
  label.setAttribute('for', 'editor-panel');
  label.textContent = 'panel';
  const select = document.createElement('select');
  select.id = 'editor-panel';
  populatePanelSelect(select, data.panels);
  select.value = summary.panel;
  // A display naming a panel this build does not have would otherwise be
  // silently moved to the first one in the list by the select itself.
  if (select.value !== summary.panel) {
    const missing = document.createElement('option');
    missing.value = summary.panel;
    missing.textContent = `${summary.panel} — not in this catalogue`;
    select.prepend(missing);
    select.value = summary.panel;
  }
  select.addEventListener('change', onEditorPanelChange);
  const help = document.createElement('div');
  help.className = 'help';
  help.textContent = node.description || '';
  const notes = document.createElement('div');
  notes.className = 'help panel-notes';
  const error = document.createElement('div');
  error.className = 'field-error';
  field.append(label, select, help, notes, error);
  editorFields.push({
    path: 'panel', section: '', name: 'panel', spec: { kind: 'text' },
    read: () => select.value || undefined, control: select,
  });
  return field;
}

/** The panel's own values are what an empty geometry override resolves to. */
function onEditorPanelChange() {
  const select = editorDialog && editorDialog.querySelector('#editor-panel');
  if (!select || !editorSummary) return;
  const panel = (formData ? formData.panels : []).find((p) => p.id === select.value) || null;
  const notes = editorDialog.querySelector('.panel-notes');
  if (notes) notes.textContent = (panel && panel.notes) || '';
  for (const name of PANEL_RESOLVED) {
    const field = editorFields.find((entry) => entry.path === name);
    if (!field) continue;
    // Unchanged panel: the summary's resolved value, which has been through
    // every override and the transport's own default. Changed: the panel
    // profile's, since that is what an empty box would now resolve to.
    let value = resolvedFor(editorSummary, name);
    if (panel && panel.id !== editorSummary.panel) {
      value = panel[name] === null || panel[name] === undefined ? '' : String(panel[name]);
      if (name === 'frame_format' && !value) value = 'from the transport';
    }
    if (field.control.tagName === 'SELECT') {
      const empty = field.control.querySelector('option[value=""]');
      if (empty) empty.textContent = value ? `panel default (${value})` : 'panel default';
    } else {
      field.control.placeholder = value;
    }
  }
}

// ----------------------------------------------------------- the transport --

/* The one section whose fields are not in the schema: `TransportConfig` is
 * `extra="allow"`, so each transport's `options_doc` is the only description
 * of its options there is. Keys the chosen transport does not document are
 * shown anyway — a key already in the file is there for a reason, and this
 * drawer is not the place to decide it was a typo. */
function transportFields(schema, data) {
  const def = deref(schema, schema.properties.transport.$ref);
  const fields = [];

  const field = document.createElement('div');
  field.className = 'field';
  field.dataset.path = 'transport.type';
  const label = document.createElement('label');
  label.setAttribute('for', 'editor-transport-type');
  label.textContent = 'type';
  const select = document.createElement('select');
  select.id = 'editor-transport-type';
  populateTransportSelect(select, data.transports);
  const current = String(editorTransport.type || 'http_pull');
  select.value = current;
  if (select.value !== current) {
    const missing = document.createElement('option');
    missing.value = current;
    missing.textContent = `${current} — not a registered transport`;
    select.prepend(missing);
    select.value = current;
  }
  select.addEventListener('change', () => {
    // Keep what is on screen before the options under it are replaced, so a
    // look at another transport does not cost the keys already typed. What is
    // on screen replaces rather than merges, or clearing a box and switching
    // away would put the old value back.
    editorTransport = Object.assign(withoutShownKeys(), collectTransport());
    editorTransport.type = select.value;
    redrawTransportOptions(data, editorDialog.querySelector('.transport-options'), select.value);
    markDirty();
  });
  const help = document.createElement('div');
  help.className = 'help';
  help.textContent = (def.properties.type || {}).description || '';
  const error = document.createElement('div');
  error.className = 'field-error';
  field.append(label, select, help, error);
  fields.push(field);

  const options = document.createElement('div');
  options.className = 'transport-options';
  fields.push(options);
  redrawTransportOptions(data, options, select.value);
  return fields;
}

function redrawTransportOptions(data, box, type) {
  const info = (data.schema.transports || {})[type] || { options: {} };
  const nodes = [];
  if (info.description) {
    const note = document.createElement('div');
    note.className = 'help';
    note.textContent = info.description;
    nodes.push(note);
  }
  if (type === 'mqtt') {
    const note = document.createElement('div');
    note.className = 'help warn';
    note.textContent = 'Needs MQTT enabled globally: mqtt.enabled: true, or the ' +
      'Mosquitto broker app under the Supervisor.';
    nodes.push(note);
  }
  const documented = Object.keys(info.options || {});
  // The stored keys this transport does not document. They are shown rather
  // than quietly carried, because `extra="allow"` means the file is the only
  // record of them and a drawer that hid them would be the thing that dropped
  // them. A key typed while another transport was selected is not one of
  // these: it stays in the working copy for a look back, and is neither shown
  // nor sent under this one.
  const extras = storedTransportKeys.filter((key) => !documented.includes(key));
  for (const key of documented) nodes.push(transportOption(key, info.options[key], false));
  for (const key of extras) nodes.push(transportOption(key, '', true));
  box.replaceChildren(...nodes);
}

function transportOption(key, description, extra) {
  const field = document.createElement('div');
  field.className = 'field';
  field.dataset.path = `transport.${key}`;
  const id = 'editor-transport-' + key;
  const label = document.createElement('label');
  label.setAttribute('for', id);
  label.textContent = key;
  const input = document.createElement('input');
  input.type = 'text';
  input.id = id;
  input.autocomplete = 'off';
  input.dataset.transportKey = key;
  const value = editorTransport[key];
  input.value = value === undefined || value === null
    ? ''
    : (typeof value === 'object' ? JSON.stringify(value) : String(value));
  const help = document.createElement('div');
  help.className = 'help' + (extra ? ' warn' : '');
  help.textContent = extra
    ? 'Not an option this transport documents. It is kept as it is; clear the ' +
      'box to drop it.'
    : description;
  field.append(label, input, help);
  return field;
}

/** The working copy minus every key the transport section is showing, which
 *  `collectTransport` is about to supply from the boxes themselves. */
function withoutShownKeys() {
  const shown = new Set(
    [...editorDialog.querySelectorAll('.transport-options [data-transport-key]')]
      .map((input) => input.dataset.transportKey)
  );
  const kept = {};
  for (const [key, value] of Object.entries(editorTransport)) {
    if (!shown.has(key)) kept[key] = value;
  }
  return kept;
}

/** The transport section as a mapping, each option in the type `parseOption`
 *  reads it as. Only the boxes on screen are in it, which is what keeps a key
 *  typed under another transport out of the body. */
function collectTransport() {
  const select = editorDialog.querySelector('#editor-transport-type');
  const transport = { type: select ? select.value : editorTransport.type };
  for (const input of editorDialog.querySelectorAll('.transport-options [data-transport-key]')) {
    const value = parseOption(input.value, editorTransport[input.dataset.transportKey]);
    if (value !== undefined) transport[input.dataset.transportKey] = value;
  }
  return transport;
}

/* Every option arrives as text and most transports coerce it themselves —
 * `float(self.option("timeout", 30))` and so on — but two shapes cannot
 * survive being a string: `write_preview` is read for truth, where "false" is
 * true, and `headers` is read as a mapping (`src/maverick/transports/pull.py`).
 * So JSON wins where it is unambiguous, and the string stands everywhere else:
 * a topic that happens to read as a number stays the text that was typed
 * unless it was a number to begin with. */
function parseOption(text, previous) {
  const raw = text.trim();
  if (raw === '') return undefined;
  if (raw === 'true') return true;
  if (raw === 'false') return false;
  if (raw.startsWith('{') || raw.startsWith('[')) {
    try { return JSON.parse(raw); } catch (e) { return raw; }
  }
  if (typeof previous === 'number' && Number.isFinite(Number(raw))) return Number(raw);
  return raw;
}

// ------------------------------------------------------------- the payload --

/** The display as the drawer has it, in the shape `PUT` and Preview take. */
function collectBody() {
  const body = { id: editorId };
  for (const field of editorFields) {
    const value = field.read();
    if (value === undefined) continue;
    if (!field.section) {
      body[field.name] = value;
    } else {
      if (!body[field.section]) body[field.section] = {};
      body[field.section][field.name] = value;
    }
  }
  body.transport = collectTransport();
  return body;
}

/** The stored value for a field, or the schema's default when the file omits it.
 *
 * `config` is `dump_display` (`src/maverick/store.py`): the shortest mapping
 * that loads back as the display, so every key left at its default is missing
 * from it. The control shows the value that is in force, not an empty box.
 */
function valueOf(section, name, node) {
  const config = (editorSummary && editorSummary.config) || {};
  const holder = section ? config[section] || {} : config;
  if (Object.prototype.hasOwnProperty.call(holder, name)) return holder[name];
  return node.default === undefined ? null : node.default;
}

function isDirty() {
  return Boolean(editorBaseline) && JSON.stringify(collectBody()) !== editorBaseline;
}

function markDirty() {
  if (!editorDialog) return;
  const pill = editorDialog.querySelector('.editor-dirty');
  if (pill) pill.hidden = !isDirty();
}

// ------------------------------------------------------------ the preview --

function previewSection(summary) {
  const node = document.createElement('section');
  node.className = 'editor-preview';
  node.innerHTML = PREVIEW;

  const now = node.querySelector('.compare-now img');
  if (summary.checksum) {
    now.setAttribute('src', withToken(
      `api/displays/${encodeURIComponent(summary.id)}/preview.png` +
      `?c=${encodeURIComponent(summary.checksum)}`
    ));
    now.alt = `current frame for ${summary.name || summary.id}`;
    now.hidden = false;
    node.querySelector('.compare-now .shot-empty').hidden = true;
  }
  node.querySelector('.act-preview').addEventListener(
    'click', (e) => runPreview(e.currentTarget)
  );
  const compare = node.querySelector('.compare');
  const mix = node.querySelector('.compare-mix');
  node.querySelector('.mode-side').addEventListener('click', () => setCompareMode(node, 'side'));
  node.querySelector('.mode-overlay').addEventListener(
    'click', () => setCompareMode(node, 'overlay')
  );
  mix.querySelector('input').addEventListener('input', (e) => {
    compare.style.setProperty('--mix', String(Number(e.currentTarget.value) / 100));
  });
  return node;
}

function setCompareMode(node, mode) {
  const compare = node.querySelector('.compare');
  compare.classList.toggle('is-side', mode === 'side');
  compare.classList.toggle('is-overlay', mode === 'overlay');
  node.querySelector('.compare-mix').hidden = mode !== 'overlay';
  node.querySelector('.mode-side').setAttribute('aria-pressed', String(mode === 'side'));
  node.querySelector('.mode-overlay').setAttribute('aria-pressed', String(mode === 'overlay'));
}

async function runPreview(button) {
  const section = editorDialog.querySelector('.editor-preview');
  const status = section.querySelector('.preview-status');
  clearEditorErrors();
  setBusy(button, true);
  setText(button, 'Rendering…');
  startTicker(status);
  try {
    const result = await sendJSON(
      'api/displays/preview', 'POST', collectBody(), 'The preview could not be rendered.'
    );
    showPreview(section, result);
  } catch (error) {
    status.textContent = '';
    if (error.unauthorised) {
      editorNeedsToken = true;
      closeEditor();
      return;
    }
    if (error.fields) applyEditorErrors(error.fields, 'The configuration is not valid yet.');
    else editorError(error.message);
  } finally {
    stopTicker();
    setBusy(button, false);
    setText(button, 'Preview');
  }
}

function showPreview(section, result) {
  const image = section.querySelector('.compare-new img');
  image.setAttribute('src', 'data:image/png;base64,' + result.preview_png);
  image.alt = 'the frame this configuration would produce';
  image.hidden = false;
  section.querySelector('.compare-new .shot-empty').hidden = true;
  section.querySelector('.compare').hidden = false;
  section.querySelector('.compare-modes').hidden = false;
  const lint = result.lint || { summary: '', issues: [] };
  section.querySelector('.preview-status').textContent =
    `${result.width}×${result.height} · rendered in ${result.render_s.toFixed(2)} s · ` +
    `${lint.summary} · nothing saved`;
  issues(section, lint.issues || []);
}

function startTicker(status) {
  stopTicker();
  const began = Date.now();
  const tick = () => {
    status.textContent = `rendering… ${Math.round((Date.now() - began) / 1000)}s`;
  };
  tick();
  previewTicker = setInterval(tick, 1000);
}

function stopTicker() {
  clearInterval(previewTicker);
  previewTicker = null;
}

// ------------------------------------------- saving, deleting and closing --

async function saveEditor(button) {
  clearEditorErrors();
  setBusy(button, true);
  try {
    await sendJSON(
      `api/displays/${encodeURIComponent(editorId)}`, 'PUT', collectBody(),
      'The display could not be saved.'
    );
    await poll();
    closeEditor();
  } catch (error) {
    if (error.unauthorised) {
      editorNeedsToken = true;
      closeEditor();
    } else if (error.fields) {
      applyEditorErrors(error.fields, 'Some fields need fixing.');
    } else {
      editorError(error.message);
    }
  } finally {
    setBusy(button, false);
  }
}

function askToDelete() {
  const name = editorSummary ? editorSummary.name || editorSummary.id : editorId;
  askFirst(
    `Delete ${name}? Its stored frames go with it, and a panel holding this ` +
    'frame keeps showing it.',
    'Delete it', deleteEditor
  );
}

async function deleteEditor() {
  const button = editorDialog.querySelector('.act-delete');
  clearEditorErrors();
  setBusy(button, true);
  try {
    await sendJSON(`api/displays/${encodeURIComponent(editorId)}`, 'DELETE', null,
      'The display could not be deleted.');
    editorDeleted = true;
    await poll();
    closeEditor();
  } catch (error) {
    if (error.unauthorised) {
      editorNeedsToken = true;
      closeEditor();
    } else {
      editorError(error.message);
    }
  } finally {
    setBusy(button, false);
  }
}

/** A question in the drawer rather than a browser dialog, which an ingress
 *  iframe is entitled to refuse to show. */
function askFirst(message, label, action) {
  const box = editorDialog.querySelector('.editor-confirm');
  editorConfirmAction = action;
  setText(box.querySelector('.editor-confirm-text'), message);
  setText(box.querySelector('.editor-confirm-yes'), label);
  box.hidden = false;
  box.querySelector('.editor-confirm-yes').focus();
}

function hideConfirm() {
  editorConfirmAction = null;
  if (!editorDialog) return;
  editorDialog.querySelector('.editor-confirm').hidden = true;
}

function requestClose() {
  if (!isDirty()) {
    closeEditor();
    return;
  }
  askFirst('Close without saving? The changes in this drawer are not on the ' +
    'display yet.', 'Discard them', closeEditor);
}

function closeEditor() {
  hideConfirm();
  if (editorDialog && editorDialog.open) editorDialog.close();
}

function afterClose() {
  stopTicker();
  const opener = editorOpener;
  editorOpener = null;
  editorFields = [];
  editorBaseline = '';
  if (editorNeedsToken) {
    editorNeedsToken = false;
    askForToken();
    return;
  }
  // The card the drawer was opened from is gone once its display is, so focus
  // goes to the one control the header always has.
  const fallback = editorDeleted ? document.getElementById('add-display-btn') : null;
  const target = fallback || (opener && document.body.contains(opener) ? opener : null);
  if (target) target.focus();
}

// ----------------------------------------------------------- field errors --

/** FastAPI's 422: `{loc: ["body", "image", "black_level"], msg}`.
 *
 * A model validator fails against the model rather than a field —
 * `ImageConfig._levels` reports at `["body", "image"]` — so the shortest
 * prefix that names something on screen wins, and the section holding the
 * first error is opened and shown.
 */
function applyEditorErrors(fields, fallback) {
  let first = null;
  for (const item of fields) {
    // `loc[0]` is the request part, always "body" here; the rest is the path
    // through `DisplayConfig`.
    const loc = (item.loc || []).slice(item.loc && item.loc[0] === 'body' ? 1 : 0);
    const message = String(item.msg || '').replace(/^Value error,\s*/, '');
    const target = errorTarget(loc);
    if (!target) {
      editorError(message || fallback);
      continue;
    }
    setText(target.querySelector('.field-error'), message);
    if (!first) first = target;
  }
  if (!first) return;
  editorError(fallback);
  const section = first.closest('.section');
  if (section) section.open = true;
  first.scrollIntoView({ block: 'center', behavior: 'auto' });
  const control = first.querySelector('input, select, textarea');
  if (control) control.focus();
}

function errorTarget(loc) {
  const parts = loc.map(String);
  while (parts.length) {
    const path = parts.join('.');
    const field = editorDialog.querySelector(`.field[data-path="${path}"]`);
    if (field) return field;
    const section = editorDialog.querySelector(`.section[data-section="${path}"]`);
    if (section) return section.querySelector('.section-body');
    parts.pop();
  }
  return null;
}

function clearEditorErrors() {
  editorError('');
  if (!editorDialog) return;
  for (const node of editorDialog.querySelectorAll('.field-error')) node.textContent = '';
}

function editorError(message) {
  if (!editorDialog) return;
  const box = editorDialog.querySelector('.editor-error');
  box.textContent = message || '';
  box.hidden = !message;
}
