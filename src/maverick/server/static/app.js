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
// its fields are fixed, unlike the per-display editor P2.3 builds from the
// schema. What this module owns is everything the markup cannot know without
// a fetch: the panel and transport lists, every field's help text (each
// node's `data-help` names a path into `GET /api/schema/display`, so the copy
// here and the description in `src/maverick/config.py` cannot drift apart),
// and turning a 422 or 409 from `POST /api/displays` into a message under the
// field it is about.

/** Cached once per page load: the three endpoints the form needs. */
let addDialogData = null;
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
  if (addDialogData) {
    onPanelChange();
    onTransportChange();
  } else {
    updatePanelNotes(null);
  }
}

async function ensureAddDialogData() {
  const submit = document.getElementById('add-submit');
  if (addDialogData) return;
  setBusy(submit, true);
  try {
    const [panelsRes, transportsRes, schemaRes] = await Promise.all([
      authFetch('api/panels'), authFetch('api/transports'), authFetch('api/schema/display'),
    ]);
    addDialogData = {
      panels: await panelsRes.json(),
      transports: await transportsRes.json(),
      schema: await schemaRes.json(),
    };
    populateAddDialog(addDialogData);
  } catch (error) {
    if (!error.unauthorised) dialogError('add-dialog-error', 'Could not load the form: ' + error.message);
  } finally {
    setBusy(submit, false);
  }
}

function populateAddDialog(data) {
  fillHelpTexts(data.schema);
  populatePanelSelect(data.panels);
  populateEnumSelect(document.getElementById('add-color-scheme'), data.schema, 'ColorScheme');
  populateEnumSelect(document.getElementById('add-frame-format'), data.schema, 'FrameFormat');
  populateTransportSelect(data.transports);
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

function populatePanelSelect(panels) {
  const select = document.getElementById('add-panel');
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

function populateTransportSelect(transports) {
  const select = document.getElementById('add-transport');
  select.replaceChildren();
  for (const t of transports) {
    const opt = document.createElement('option');
    opt.value = t.name;
    opt.textContent = t.name;
    opt.title = t.description;
    select.appendChild(opt);
  }
}

/** An enum <select>, `""` meaning "let the panel decide" — its label gets the panel's value. */
function populateEnumSelect(select, schema, defName) {
  select.replaceChildren();
  const unset = document.createElement('option');
  unset.value = '';
  unset.textContent = 'panel default';
  select.appendChild(unset);
  for (const value of (schema.$defs[defName] || {}).enum || []) {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = value;
    select.appendChild(opt);
  }
}

function currentPanel() {
  if (!addDialogData) return null;
  const id = document.getElementById('add-panel').value;
  return addDialogData.panels.find((p) => p.id === id) || null;
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
  if (!addDialogData) return;
  const type = document.getElementById('add-transport').value;
  const info = addDialogData.schema.transports[type];
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

/** Like `authFetch`, but keeps the structured 422/409 body instead of flattening it. */
async function postDisplay(body) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, authHeaders());
  let response;
  try {
    response = await fetch('api/displays', { method: 'POST', headers, body: JSON.stringify(body) });
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
  if (response.status === 201) return response.json();
  let detail = null;
  try { detail = (await response.json()).detail; } catch (e) { /* no body */ }
  const error = new Error(typeof detail === 'string' ? detail : 'The display could not be added.');
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
    const created = await postDisplay(buildAddBody());
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
