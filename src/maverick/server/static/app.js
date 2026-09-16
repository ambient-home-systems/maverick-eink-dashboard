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
/** display id -> 'frame' or 'source', for the card's Source/Frame toggle. Not
 *  persisted: every card opens on Frame, same as the editor's preview pane. */
const viewMode = new Map();
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

/* A page change costs a whole render, so the request answers as soon as the
 * page has moved and the render runs behind it — the same bargain as the
 * Refresh button, and why both mark the display pending here. */
function postPage(id, body, control) {
  return act(control, async () => {
    await authFetch(`api/displays/${encodeURIComponent(id)}/page`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    pending.set(id, { at: Date.now(), seen: false });
  });
}

function stepPage(id, step, button) {
  return postPage(id, { step: step }, button);
}

function selectPage(id, index, control) {
  return postPage(id, { index: index }, control);
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
<div class="page-picker" role="group" aria-label="Page" hidden>
  <button type="button" class="page-prev" title="Previous page" aria-label="Previous page">&lsaquo;</button>
  <select class="page-select" aria-label="Page"></select>
  <button type="button" class="page-next" title="Next page" aria-label="Next page">&rsaquo;</button>
</div>
<span class="view-modes" role="group" aria-label="Source or result" hidden>
  <button type="button" class="view-source" aria-pressed="false">Source</button>
  <button type="button" class="view-frame" aria-pressed="true">Frame</button>
  <button type="button" class="view-full" title="See it at the panel's own size">Full size</button>
</span>
<img class="shot is-clickable" alt="" loading="lazy" role="button" tabindex="0"
     title="See it at the panel's own size" hidden>
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
</div>
<div class="meta err card-error" hidden></div>
<ul class="issues"></ul>
<details class="history">
  <summary>History</summary>
  <div class="history-body">
    <table class="history-table" hidden>
      <thead><tr><th>Time</th><th>Trigger</th><th>Outcome</th><th>Duration</th><th>Lint</th></tr></thead>
      <tbody></tbody>
    </table>
    <p class="history-empty muted" hidden>No renders yet.</p>
  </div>
</details>
<details class="starter">
  <summary>Dashboard starter</summary>
  <div class="starter-body">
    <p class="meta starter-intro">A Lovelace dashboard sized for this panel:
      the column count and how much text fits come from its own pixels and dpi,
      and every card in it is one that survives dithering. Paste it into Home
      Assistant under Settings &rarr; Dashboards &rarr; Add dashboard, then
      Edit &rarr; &#8942; &rarr; Raw configuration editor.</p>
    <div class="row">
      <button type="button" class="starter-copy">Copy</button>
      <a class="starter-download" download>Download</a>
      <a class="starter-guide" target="_blank" rel="noopener"
         href="https://github.com/ambient-home-systems/maverick-eink-dashboard/blob/main/docs/design-guide.md"
      >Designing for e-ink</a>
    </div>
    <p class="meta err starter-error" hidden></p>
    <pre class="starter-yaml" tabindex="0"></pre>
  </div>
</details>
<details class="install" hidden>
  <summary>Install on device <span class="badge">ESPHome</span></summary>
  <div class="install-body">
    <p class="meta err install-error" hidden></p>
    <div class="install-steps"></div>
  </div>
</details>`;

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
  button('.page-prev').addEventListener('click', (e) => stepPage(id, -1, e.currentTarget));
  button('.page-next').addEventListener('click', (e) => stepPage(id, 1, e.currentTarget));
  button('.page-select').addEventListener('change', (e) => {
    selectPage(id, Number(e.currentTarget.value), e.currentTarget);
  });
  button('.view-source').addEventListener('click', () => setViewMode(id, 'source'));
  button('.view-frame').addEventListener('click', () => setViewMode(id, 'frame'));
  button('.view-full').addEventListener('click', (e) => openFullSize(id, e.currentTarget));
  // The thumbnail is the obvious thing to click, so it does what the button
  // does. `role`/`tabindex` rather than wrapping it in a <button>, which would
  // put a second focus stop on every card for the same action.
  const shot = button('img.shot');
  shot.addEventListener('click', (e) => openFullSize(id, e.currentTarget));
  shot.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      openFullSize(id, e.currentTarget);
    }
  });
  // Loads on open, same as every other on-demand fetch here; `paint` below
  // refreshes it again on every poll while it stays open.
  card.querySelector('.history').addEventListener('toggle', (e) => {
    if (e.currentTarget.open) loadHistory(id, card);
  });
  // Fetched once and kept: the generated dashboard depends on the panel and
  // on the entity list, neither of which changes while the page is open, and
  // re-fetching it under a user who is mid-copy would be worse than stale.
  card.querySelector('.starter').addEventListener('toggle', (e) => {
    if (e.currentTarget.open) loadStarter(id, card);
  });
  card.querySelector('.starter-copy').addEventListener('click', (e) => {
    copyText(card.querySelector('.starter-yaml'), e.currentTarget);
  });
  // The guided ESPHome hand-off, fetched on first open like the starter. It
  // is re-fetched on every open rather than kept: `secrets.yaml` and the
  // Device Builder's folder change while the page is open, and that is
  // exactly what the step reports on.
  card.querySelector('.install').addEventListener('toggle', (e) => {
    if (e.currentTarget.open) loadInstall(id, card);
  });
  cards.set(id, card);
  return card;
}

/** `GET /api/displays/{id}/dashboard.yaml`, once per card.
 *
 *  The route answers with placeholder entity ids rather than failing when
 *  Home Assistant cannot be read (`src/maverick/server/api.py`), so there is
 *  no "not connected" case to handle here — only a real transport failure. */
async function loadStarter(id, card) {
  const box = card.querySelector('.starter-yaml');
  if (box.dataset.loaded) return;
  const error = card.querySelector('.starter-error');
  error.hidden = true;
  box.textContent = 'Generating…';
  try {
    const response = await authFetch(
      `api/displays/${encodeURIComponent(id)}/dashboard.yaml`
    );
    box.textContent = await response.text();
    box.dataset.loaded = '1';
    // A blob rather than the route itself: an <a download> cannot send the
    // Authorization header, and putting the token in the URL would leave it
    // in the browser's download history.
    const url = URL.createObjectURL(new Blob([box.textContent], { type: 'text/yaml' }));
    const link = card.querySelector('.starter-download');
    link.href = url;
    link.setAttribute('download', `${id}-dashboard.yaml`);
  } catch (e) {
    box.textContent = '';
    if (!e.unauthorised) {
      error.textContent = 'Could not generate the dashboard: ' + e.message;
      error.hidden = false;
    }
  }
}

/** Copy a box's text, with the selection as the fallback.
 *
 *  `navigator.clipboard` is unavailable on a plain-HTTP origin, which is
 *  exactly how Maverick is reached on a LAN, so the button selects the text
 *  instead and says so rather than appearing to do nothing. */
async function copyText(box, button) {
  if (!box || !box.textContent) return;
  const done = (message) => {
    const was = button.textContent;
    button.textContent = message;
    setTimeout(() => { button.textContent = was; }, 2000);
  };
  try {
    await navigator.clipboard.writeText(box.textContent);
    done('Copied');
  } catch (e) {
    const range = document.createRange();
    range.selectNodeContents(box);
    const selection = getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    box.focus();
    done('Selected — press Ctrl+C');
  }
}

// ------------------------------------------------------ the full-size view --

/* A card is one column of a responsive grid — about 345px wide on a laptop —
 * so an 800×480 frame is shown at well under half size and a 1872×1404 one at
 * a fifth. That is fine for "did it render", and useless for the question this
 * page exists to answer: is the result legible. Worse, a thumbnail that small
 * makes a frame whose *content* runs off the panel look identical to one that
 * fits, because both are just small.
 *
 * So: the same image, at the panel's own pixel size, with the window as the
 * only limit. `image-rendering: pixelated` matters here — at 1:1 a browser's
 * smoothing invents grey between two inks the panel cannot print, which is the
 * opposite of what someone checking a dithered frame needs to see.
 */
let fullDialog = null;
let fullOpener = null;

function ensureFullDialog() {
  if (fullDialog) return fullDialog;
  const dialog = document.createElement('dialog');
  dialog.id = 'fullsize';
  dialog.innerHTML = `
<div class="full-bar">
  <span class="full-title"></span>
  <span class="full-meta"></span>
  <span class="full-actions">
    <button type="button" class="full-fit" aria-pressed="false">Fit to window</button>
    <a class="full-open" target="_blank" rel="noopener">Open PNG</a>
    <button type="button" class="full-close">Close</button>
  </span>
</div>
<div class="full-body"><img class="full-img" alt=""></div>`;
  document.body.appendChild(dialog);
  dialog.querySelector('.full-close').addEventListener('click', () => dialog.close());
  dialog.querySelector('.full-fit').addEventListener('click', (e) => {
    const on = dialog.classList.toggle('is-fit');
    e.currentTarget.setAttribute('aria-pressed', String(on));
  });
  // Clicking the backdrop closes it: the click lands on the <dialog> itself
  // rather than on anything inside, which is what distinguishes the two.
  dialog.addEventListener('click', (e) => {
    if (e.target === dialog) dialog.close();
  });
  dialog.addEventListener('close', () => {
    if (fullOpener && document.body.contains(fullOpener)) fullOpener.focus();
    fullOpener = null;
  });
  fullDialog = dialog;
  return dialog;
}

/** Open the frame this card is showing, at 1:1.
 *
 *  Reuses the card's own `<img>` src rather than re-deriving the URL, so the
 *  Source/Frame toggle and the checksum cache-buster carry over and the
 *  browser serves it from cache instead of re-fetching. */
function openFullSize(id, opener) {
  const card = cards.get(id);
  const display = state.find((d) => d.id === id);
  if (!card || !display || !display.checksum) return;
  const source = card.querySelector('img.shot');
  if (!source || !source.getAttribute('src')) return;

  const dialog = ensureFullDialog();
  fullOpener = opener || document.activeElement;
  const mode = viewMode.get(id) || 'frame';
  const image = dialog.querySelector('.full-img');
  image.setAttribute('src', source.getAttribute('src'));
  image.alt = source.alt;
  setText(dialog.querySelector('.full-title'), display.name);
  // What the panel is, so the pixel size on screen means something. The
  // `source` capture is downscaled to panel resolution before it is stored
  // (`GET /api/displays/{id}/screenshot.png`), so both modes are this size.
  setText(
    dialog.querySelector('.full-meta'),
    `${mode === 'source' ? 'source render' : 'panel frame'} · ` +
    `${display.width}×${display.height} · ${display.color_scheme} · ${display.dpi} dpi`
  );
  const link = dialog.querySelector('.full-open');
  link.setAttribute('href', source.getAttribute('src'));
  // Start at 1:1 every time: "fit" is the fallback for a frame bigger than the
  // window, not the default, because 1:1 is the whole point.
  dialog.classList.remove('is-fit');
  dialog.querySelector('.full-fit').setAttribute('aria-pressed', 'false');
  if (!dialog.open) dialog.showModal();
}

function setViewMode(id, mode) {
  viewMode.set(id, mode);
  const display = state.find((d) => d.id === id);
  if (display) preview(cards.get(id), display);
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
  // With pages, what the card should name is the page on the panel, not the
  // `dashboard` shorthand the display does not use.
  const page = display.page || null;
  text(card, '.card-dashboard', page && page.count > 1
    ? `${page.name} · ${page.dashboard}`
    : display.dashboard);
  text(card, '.card-transport', display.transport);
  text(card, '.card-schedule', scheduleSummary(display.schedule));

  const pill = card.querySelector('.pill');
  pill.className = 'pill ' + pillClass;
  text(card, '.pill', pillText);
  card.classList.toggle('is-disabled', !display.enabled);

  pagePicker(card, display);
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

  // The ESPHome step is for a panel an ESP32 plausibly drives over a pull
  // transport (`esphome_applicable`, `src/maverick/esphome/generator.py`); a
  // Kindle or a BLE tag gets no firmware config to install.
  card.querySelector('.install').hidden = !display.esphome_applicable;

  const error = card.querySelector('.card-error');
  // 300 characters: a Playwright failure runs to pages, and the whole of it
  // is in the log and in `state.last_error` either way.
  const message = (display.state && display.state.last_error) || '';
  error.hidden = !message;
  text(card, '.card-error', message.slice(0, 300));

  issues(card, display.lint ? display.lint.issues : []);

  const history = card.querySelector('.history');
  if (history.open) loadHistory(display.id, card);
}

/** The page picker, for a display that has pages to pick between.
 *
 * A display with no `pages` has exactly one page — its `dashboard` — and the
 * summary says so (`count: 1`), so "has pages" is a count rather than a flag
 * this has to be told about separately.
 */
function pagePicker(card, display) {
  const box = card.querySelector('.page-picker');
  const page = display.page || null;
  if (!page || page.count < 2) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const select = box.querySelector('.page-select');
  // Rebuilding the options on every poll would drop whatever the keyboard was
  // doing inside the select, so they are rebuilt only when they change.
  const names = page.names.join('\n');
  if (select.dataset.names !== names) {
    select.dataset.names = names;
    select.replaceChildren(...page.names.map((name, index) => {
      const option = document.createElement('option');
      option.value = String(index);
      option.textContent = name;
      return option;
    }));
  }
  const wanted = String(page.index);
  if (select.value !== wanted) select.value = wanted;
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
  const toggle = card.querySelector('.view-modes');
  if (!display.checksum) {
    image.hidden = true;
    placeholder.hidden = false;
    toggle.hidden = true;
    return;
  }
  toggle.hidden = false;
  const mode = viewMode.get(display.id) || 'frame';
  toggle.querySelector('.view-source').setAttribute('aria-pressed', String(mode === 'source'));
  toggle.querySelector('.view-frame').setAttribute('aria-pressed', String(mode === 'frame'));
  // The preview URL is stable but what it serves is not, so the checksum goes
  // in the query: the browser re-fetches when the frame or the toggle
  // changes, and only then. setAttribute rather than .src, which would
  // resolve to an absolute URL and lose the ingress prefix. Two literal
  // targets rather than one with the filename interpolated in, so a typo in
  // either fails the relative-path sweep in tests/test_setup_ui.py by name.
  const key = `${mode}:${display.checksum}`;
  if (image.dataset.key !== key) {
    image.dataset.key = key;
    const url = mode === 'source'
      ? `api/displays/${encodeURIComponent(display.id)}/screenshot.png` +
        `?c=${encodeURIComponent(display.checksum)}`
      : `api/displays/${encodeURIComponent(display.id)}/preview.png` +
        `?c=${encodeURIComponent(display.checksum)}`;
    image.setAttribute('src', withToken(url));
  }
  image.alt = mode === 'source'
    ? `source render for ${display.name}`
    : `current frame for ${display.name}`;
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

// -------------------------------------------------------------- history --

async function loadHistory(id, card) {
  try {
    const response = await authFetch(`api/displays/${encodeURIComponent(id)}/history?limit=20`);
    paintHistory(card, await response.json());
  } catch (error) {
    // A 401 already opened the token field; the poll or the next open tries
    // again. Anything else is worth a line, same as any other failed fetch.
    if (!error.unauthorised) report(error);
  }
}

function paintHistory(card, rows) {
  const table = card.querySelector('.history-table');
  const empty = card.querySelector('.history-empty');
  const signature = JSON.stringify(rows);
  if (table.dataset.signature === signature) return;
  table.dataset.signature = signature;
  if (!rows.length) {
    table.hidden = true;
    empty.hidden = false;
    table.querySelector('tbody').replaceChildren();
    return;
  }
  empty.hidden = true;
  table.hidden = false;
  table.querySelector('tbody').replaceChildren(...rows.map(historyRow));
}

/** "failed", "blocked", "skipped" or "ok" — what a history row's pill says. */
function historyOutcome(row) {
  if (!row.ok) return 'failed';
  if (row.skipped) return row.reason.startsWith('blocked by lint') ? 'blocked' : 'skipped';
  return 'ok';
}

/** One history row. A failed or blocked pill is a button that reveals a
 *  second, hidden row with the reason — an ordinary skip or success has
 *  nothing worth a click. */
function historyRow(row) {
  const outcome = historyOutcome(row);
  const highlighted = outcome === 'failed' || outcome === 'blocked';
  const expandable = highlighted && Boolean(row.reason);

  const tr = document.createElement('tr');
  tr.className = 'history-row' + (highlighted ? ' err' : '');

  const time = document.createElement('td');
  time.textContent = relative(row.at);
  time.title = absolute(row.at);
  tr.append(time);

  const trigger = document.createElement('td');
  trigger.textContent = row.trigger;
  tr.append(trigger);

  const outcomeCell = document.createElement('td');
  const pill = document.createElement(expandable ? 'button' : 'span');
  if (expandable) pill.type = 'button';
  pill.className = 'pill history-pill ' + (highlighted ? 'err' : outcome === 'ok' ? 'ok' : 'muted');
  pill.textContent = outcome;
  outcomeCell.append(pill);
  tr.append(outcomeCell);

  const duration = document.createElement('td');
  duration.textContent = row.total_s ? `${row.total_s.toFixed(2)} s` : '';
  tr.append(duration);

  const lint = document.createElement('td');
  lint.textContent = row.lint_summary || '';
  tr.append(lint);

  const fragment = document.createDocumentFragment();
  fragment.append(tr);

  if (expandable) {
    const reasonRow = document.createElement('tr');
    reasonRow.className = 'history-reason';
    reasonRow.hidden = true;
    const cell = document.createElement('td');
    cell.colSpan = 5;
    cell.textContent = row.reason;
    reasonRow.append(cell);
    pill.setAttribute('aria-expanded', 'false');
    pill.addEventListener('click', () => {
      reasonRow.hidden = !reasonRow.hidden;
      pill.setAttribute('aria-expanded', String(!reasonRow.hidden));
    });
    fragment.append(reasonRow);
  }

  return fragment;
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
/** Whether the user has chosen a panel themselves, so a picked tag's guess
 *  no longer moves the picker. */
let addPanelEdited = false;

function slugify(value) {
  return (value || '')
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9_-]/g, '')
    .replace(/^[^a-z0-9]+/, '');
}

/** Open the dialog, optionally with a display already sketched in.
 *
 *  `prefill` is what a discovered tag knows about itself
 *  (`GET /api/ha/opendisplay/devices`): a name, a guessed panel and the
 *  transport options — the device registry id above all — so the user's job
 *  is to confirm rather than to find. Every prefilled field stays editable. */
async function openAddDialog(opener, prefill) {
  const dialog = document.getElementById('add-dialog');
  if (!dialog) return;
  addDialogOpener = opener || document.activeElement;
  resetAddForm();
  dialog.showModal();
  document.getElementById('add-name').focus();
  await ensureAddDialogData();
  if (prefill) applyPrefill(prefill);
  populateDashboardList(document.getElementById('add-dashboard-list'));
}

function applyPrefill(prefill) {
  if (prefill.name) {
    document.getElementById('add-name').value = prefill.name;
    document.getElementById('add-id').value = slugify(prefill.name);
  }
  const panel = document.getElementById('add-panel');
  if (prefill.panel && [...panel.options].some((o) => o.value === prefill.panel)) {
    panel.value = prefill.panel;
    addPanelEdited = true;
  }
  const transport = Object.assign({}, prefill.transport || {});
  const type = transport.type || '';
  delete transport.type;
  const picker = document.getElementById('add-transport');
  // Only when the panel would not pick the same transport by itself: an
  // absent `type` is what keeps the display following its panel.
  const chosen = currentPanel();
  picker.value = chosen && chosen.default_transport === type ? '' : type;
  addTransportValues = transport;
  onPanelChange();
  document.getElementById('add-dashboard').focus();
}

function resetAddForm() {
  const form = document.getElementById('add-form');
  if (form) form.reset();
  addIdEdited = false;
  addPanelEdited = false;
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
  const advanced = document.getElementById('add-advanced');
  if (advanced) advanced.open = false;
  addTransportValues = {};
  probeResult(document.getElementById('add-test-result'), null);
  if (addDialogPopulated) {
    onPanelChange();
  } else {
    updatePanelNotes(null);
    updatePanelSummary(null);
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
    environment: await loadEnvironment(),
  };
  return formData;
}

/** `GET /api/environment`: what this host can do — scan for tags, reach the
 *  ESPHome Device Builder — and whether it is the Home Assistant app, where
 *  `mode: ble` is never available. A failure leaves the conservative answer,
 *  which draws the forms as they were before the route existed. */
async function loadEnvironment() {
  try {
    const response = await authFetch('api/environment');
    return await response.json();
  } catch (error) {
    return { addon: false, bluetooth_scan: false, esphome_dashboard: null, esphome_destinations: [] };
  }
}

/** Cached once per page load: `GET /api/ha/dashboards`, or `[]` when it is
 *  unavailable (no Home Assistant connection, or an old server without the
 *  route). Either way the Dashboard field stays a plain text input — this
 *  only ever adds suggestions to it, never replaces it. */
let dashboardsCache = null;
/** The tags Home Assistant's OpenDisplay integration knows, cached per page
 *  load and refreshed on demand (`GET /api/ha/opendisplay/devices`). `null`
 *  until asked; `[]` when Home Assistant is not connected. Declared up here
 *  with the other caches: `loadDiscovery` runs at start-up, before the
 *  module's later declarations would have been reached. */
let devicesCache = null;

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

/** Whether `populateAddDialog` has filled this dialog's selects yet.
 *
 *  Not `formData`, which is shared with the editor drawer: opening an editor
 *  first caches it, and guarding on it here then skipped the one call that
 *  puts options into *this* dialog's <select>s, leaving the panel and
 *  transport pickers empty for the rest of the page's life. What this has to
 *  track is whether the dialog itself has been populated. */
let addDialogPopulated = false;

async function ensureAddDialogData() {
  const submit = document.getElementById('add-submit');
  if (addDialogPopulated) return;
  setBusy(submit, true);
  try {
    populateAddDialog(await ensureFormData());
    addDialogPopulated = true;
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

/** The Add dialog's transport picker, whose first option is "let the panel
 *  decide" and is what a display gets by leaving `transport.type` out
 *  (`DisplayConfig.transport_type`, `src/maverick/config.py`). Its label picks
 *  up the selected panel's own default in `updatePanelSummary`, so the
 *  automatic answer is legible rather than merely implied. */
function populateTransportSelect(select, transports, autoLabel) {
  select.replaceChildren();
  const auto = document.createElement('option');
  auto.value = '';
  auto.textContent = autoLabel || 'automatic (from panel)';
  select.appendChild(auto);
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
  updatePanelSummary(panel);
  updateAdvancedPlaceholders(panel);
  // The transport's own option fields belong to whichever transport is
  // *effective*, and on `automatic` that is the panel's — so changing the
  // panel changes them.
  onTransportChange();
}

/** One line under the panel picker saying what this panel has settled without
 *  being asked: its geometry, its inks and the transport it is reached over.
 *  The Advanced fold below is then somewhere to disagree with a stated answer
 *  rather than somewhere a user has to go and guess. */
function updatePanelSummary(panel) {
  const summary = document.getElementById('add-panel-summary');
  if (!summary) return;
  if (!panel) {
    summary.textContent = '';
    return;
  }
  summary.textContent =
    `${panel.width}×${panel.height}, ${panel.color_scheme}, ${panel.dpi} dpi, ` +
    `delivered over ${panel.default_transport}. Change any of it under Advanced.`;
  const auto = document.querySelector('#add-transport option[value=""]');
  if (auto) auto.textContent = `automatic — ${panel.default_transport} for this panel`;
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

/** The transport this form would actually use: the picker's value, or the
 *  selected panel's `default_transport` while the picker is on `automatic`.
 *  Mirrors `DisplayConfig.transport_type` (`src/maverick/config.py`); the two
 *  have to agree, because this is what draws the option fields the user then
 *  fills in. */
function effectiveTransport() {
  const chosen = document.getElementById('add-transport').value;
  if (chosen) return chosen;
  const panel = currentPanel();
  return panel ? panel.default_transport : '';
}

/** The transport options typed so far, kept across redraws: a change of
 *  mode or of panel rebuilds the controls, and should not cost what was in
 *  them. Prefilled by a discovered tag (`applyPrefill`). */
let addTransportValues = {};

/** Draw the effective transport's options in two places.
 *
 *  What the transport cannot deliver without — its mode and its required
 *  options — goes above the Advanced fold, into `#add-delivery`, because a
 *  BLE tag with no device id is a display that fails on its first schedule
 *  and the fold is where that used to hide. Everything optional goes under
 *  Advanced as before. Both are drawn by `transportControls`, from the
 *  transport's own `option_fields` (`src/maverick/transports/base.py`). */
function onTransportChange() {
  const container = document.getElementById('add-transport-options');
  const delivery = document.getElementById('add-delivery');
  const deliveryBox = document.getElementById('add-delivery-options');
  const help = document.getElementById('add-transport-help');
  if (!formData) return;
  // Keep what is on screen before it is replaced.
  Object.assign(addTransportValues, readTransportInputs(document.getElementById('add-dialog')));
  container.replaceChildren();
  deliveryBox.replaceChildren();
  const type = effectiveTransport();
  const info = formData.schema.transports[type];
  help.textContent = (info && info.description) || '';
  if (!info) {
    delivery.hidden = true;
    return;
  }
  if (type === 'mqtt') {
    const note = document.createElement('div');
    note.className = 'help warn';
    note.textContent = 'Needs MQTT enabled globally: mqtt.enabled: true, or the ' +
      'Mosquitto broker app under the Supervisor.';
    container.appendChild(note);
  }
  const built = transportControls(info, addTransportValues, {
    idPrefix: 'add-opt',
    environment: formData.environment,
    extras: [],
    onModeChange: onTransportChange,
    // A picked tag names its size; move the panel picker to the guess unless
    // the user has already chosen one themselves.
    onPanelGuess: (guess) => {
      const panel = document.getElementById('add-panel');
      if (addPanelEdited || panel.value === guess) return;
      if (![...panel.options].some((o) => o.value === guess)) return;
      panel.value = guess;
      onPanelChange();
    },
  });
  delivery.hidden = !built.primary.length;
  document.getElementById('add-delivery-help').textContent = built.primary.length
    ? `How the frame reaches a ${type} panel.`
    : '';
  deliveryBox.append(...built.primary);
  if (built.advanced) container.appendChild(built.advanced);
  if (!built.primary.length && !built.advanced) {
    const note = document.createElement('div');
    note.className = 'help';
    note.textContent = 'This transport takes no options of its own.';
    container.appendChild(note);
  }
}

/** Every `[data-transport-key]` control under `root`, as text, empty ones left out. */
function readTransportInputs(root) {
  const values = {};
  if (!root) return values;
  for (const input of root.querySelectorAll('[data-transport-key]')) {
    const value = input.value.trim();
    if (value) values[input.dataset.transportKey] = value;
  }
  return values;
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
  // Most of the form is folded away now, and a message inside a closed
  // `<details>` is a form that rejects a submission and appears to say
  // nothing. Anything that failed is worth the fold opening for.
  const advanced = document.getElementById('add-advanced');
  if (message && advanced && advanced.contains(el)) advanced.open = true;
}

function buildAddBody() {
  const value = (id) => document.getElementById(id).value.trim();
  const schedule = {};
  // `every` and `cron` are mutually exclusive in the model
  // (`ScheduleConfig._exclusive`, `src/maverick/config.py`), so the form sends
  // one of them and never both: a crontab under Advanced is the considered
  // answer and replaces the interval picker, which is what its help says.
  if (value('add-cron')) {
    schedule.cron = value('add-cron');
  } else if (value('add-refresh')) {
    schedule.every = value('add-refresh');
  }
  if (value('add-quiet-hours')) schedule.quiet_hours = value('add-quiet-hours');
  const onChange = value('add-on-change').split(',').map((s) => s.trim()).filter(Boolean);
  if (onChange.length) schedule.on_change = onChange;

  // No `type` at all when the picker is on `automatic`: an absent one is what
  // makes the panel's own `default_transport` apply
  // (`DisplayConfig.transport_type`). Sending the resolved name instead would
  // pin the display to today's catalogue entry.
  const transport = {};
  if (value('add-transport')) transport.type = value('add-transport');
  // Both containers: the required options above the fold and the rest under
  // it (`onTransportChange`).
  for (const input of document.querySelectorAll('#add-dialog [data-transport-key]')) {
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
  // `id` carries no `required` attribute: it lives under the Advanced fold
  // now, and the browser cannot report a constraint violation on a control it
  // cannot focus — the submission would be blocked with nothing shown. It is
  // derived from the name, so the only way to arrive here without one is an
  // empty name.
  const id = document.getElementById('add-id');
  if (!id.value.trim()) {
    fieldError('id', 'Give the display a name, or set an id here: it becomes the URL path.');
    document.getElementById('add-name').focus();
    return;
  }
  if (!/^[a-z0-9][a-z0-9_-]*$/.test(id.value.trim())) {
    fieldError('id', 'Lower-case letters, digits, - or _, starting with a letter or digit.');
    id.focus();
    return;
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
    loadDiscovery();
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
  // the preview images — which cannot send a header — get it appended to
  // their URLs.
  const supplied = new URLSearchParams(location.search).get('token');
  if (supplied) storeToken(supplied);

  const initial = document.getElementById('initial-displays');
  if (initial) {
    try { state = JSON.parse(initial.textContent); } catch (e) { state = []; }
  }
  paintAll();
  schedulePoll();
  loadDiscovery();
  document.getElementById('discovery-refresh')?.addEventListener('click', (e) => {
    act(e.currentTarget, loadDiscovery);
  });

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
  document.getElementById('add-panel').addEventListener('change', () => {
    addPanelEdited = true;
    onPanelChange();
  });
  document.getElementById('add-transport').addEventListener('change', onTransportChange);
  document.getElementById('add-name').addEventListener('input', (e) => {
    if (!addIdEdited) document.getElementById('add-id').value = slugify(e.target.value);
  });
  document.getElementById('add-id').addEventListener('input', () => { addIdEdited = true; });
  document.getElementById('add-test').addEventListener('click', (e) => {
    probeCandidate(buildAddBody(), document.getElementById('add-test-result'), e.currentTarget)
      .then((error) => {
        if (error && error.fields) applyFieldErrors(error.fields);
      });
  });
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
  <span class="view-modes" role="group" aria-label="Source or result">
    <button type="button" class="view-source" aria-pressed="false">Source</button>
    <button type="button" class="view-frame" aria-pressed="true">Frame</button>
  </span>
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
  parts.push(pagesSection(schema, summary));
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
    // `pages` and `rotate` have a section of their own, below this one.
    (key) => key !== 'id' && !isSection(schema, key) && !PAGE_FIELDS.has(key)
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

/* ------------------------------------------------------------- the pages --
 *
 * `pages` is a list of models, which is the one shape the generated controls
 * cannot draw: `specFor` would see an array and offer a comma-separated text
 * box, and a page is three fields and an order. So `pages` and `rotate` are
 * lifted out of the Display section into one of their own, built by hand and
 * registered in `editorFields` like everything else, so `collectBody` and the
 * dirty check need to know nothing about it.
 */

/** The two fields the Pages section owns, and so the Display section skips. */
const PAGE_FIELDS = new Set(['pages', 'rotate']);

/** The rule the schema cannot state in one field's description: it is about
 *  this field and the Dashboard box in the section above. */
const PAGES_HELP =
  'Set these or the Dashboard field above, not both — a display with pages ' +
  'renders those, and saving with both is refused. A row with no dashboard is ' +
  'dropped on save, and a page with no name takes one from its dashboard path.';

function pagesSection(schema, summary) {
  const fields = [
    pagesField(schema, summary),
    fieldNode(schema, '', 'rotate', schema.properties.rotate, ''),
  ];
  // The section's own help is what the schema says about `pages`, as every
  // other section shows its model's description.
  return sectionNode(
    'pages', 'Pages', fields, false, schema.properties.pages.description || ''
  );
}

function pagesField(schema, summary) {
  const field = document.createElement('div');
  field.className = 'field';
  // The path a 422 against the list — or against one page — is put back under
  // (`applyEditorErrors` walks up from `pages.0.dashboard` to `pages`).
  field.dataset.path = 'pages';

  const rows = document.createElement('div');
  rows.className = 'pages-rows';
  for (const page of (summary.config && summary.config.pages) || []) {
    rows.appendChild(pageRow(page));
  }

  const add = document.createElement('button');
  add.type = 'button';
  add.className = 'palette-add';
  add.textContent = 'Add a page';
  add.addEventListener('click', () => {
    const row = pageRow({});
    rows.appendChild(row);
    row.querySelector('.page-dashboard').focus();
    markDirty();
  });

  const help = document.createElement('div');
  help.className = 'help';
  help.textContent = PAGES_HELP;
  const error = document.createElement('div');
  error.className = 'field-error';
  field.append(rows, add, help, error);

  editorFields.push({
    path: 'pages',
    section: '',
    name: 'pages',
    spec: { kind: 'pages' },
    // Undefined for an empty list, the same as every other empty control: the
    // key is left out of the body rather than sent as `[]`.
    read: () => {
      const pages = [...rows.querySelectorAll('.page-row')].map(readPageRow).filter(Boolean);
      return pages.length ? pages : undefined;
    },
    control: rows,
  });
  return field;
}

function pageRow(page) {
  const row = document.createElement('div');
  row.className = 'page-row';

  const dashboard = pageInput('page-dashboard', 'dashboard', page.dashboard);
  // The same picker the Dashboard field above uses, from the drawer's own
  // datalist.
  dashboard.setAttribute('list', 'editor-dashboard-list');
  dashboard.placeholder = '/lovelace-eink/kitchen';
  const name = pageInput('page-name', 'page name', page.name);
  name.placeholder = 'from the dashboard path';
  const dwell = pageInput('page-dwell', 'dwell', page.dwell);
  dwell.placeholder = 'every render';

  const move = (step) => {
    const sibling = step < 0 ? row.previousElementSibling : row.nextElementSibling;
    if (!sibling) return;
    // Rotation follows the order of the list, so moving a row is a change to
    // what the panel shows next, not only to how this drawer looks.
    if (step < 0) sibling.before(row);
    else sibling.after(row);
    markDirty();
  };
  const up = pageButton('page-up', 'move this page up', '↑', () => move(-1));
  const down = pageButton('page-down', 'move this page down', '↓', () => move(1));
  const remove = pageButton('palette-remove', 'remove this page', '×', () => {
    row.remove();
    markDirty();
  });

  row.append(dashboard, name, dwell, up, down, remove);
  return row;
}

function pageInput(className, label, value) {
  const input = document.createElement('input');
  input.type = 'text';
  input.className = className;
  input.autocomplete = 'off';
  input.setAttribute('aria-label', label);
  input.value = value === undefined || value === null ? '' : String(value);
  return input;
}

function pageButton(className, label, glyph, action) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = className;
  button.setAttribute('aria-label', label);
  button.title = label;
  button.textContent = glyph;
  button.addEventListener('click', action);
  return button;
}

/** One row as `PageConfig` takes it, or null for a row with no dashboard.
 *
 * An empty `name` or `dwell` is left out rather than sent as an empty string:
 * the name is derived from the dashboard path and the dwell means "every
 * render" when it is absent (`PageConfig`, `src/maverick/config.py`).
 */
function readPageRow(row) {
  const value = (selector) => row.querySelector(selector).value.trim();
  const dashboard = value('.page-dashboard');
  if (!dashboard) return null;
  const page = { dashboard: dashboard };
  const name = value('.page-name');
  if (name) page.name = name;
  const dwell = value('.page-dwell');
  if (dwell) page.dwell = dwell;
  return page;
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
  // The transport is a panel-resolved value too while the picker is on
  // `automatic`, so a new panel renames that option and redraws the option
  // fields underneath it.
  const transport = editorDialog.querySelector('#editor-transport-type');
  if (transport) {
    const auto = transport.querySelector('option[value=""]');
    if (auto) auto.textContent = editorAutoTransportLabel();
    if (!transport.value && formData) {
      redrawTransportOptions(
        formData, editorDialog.querySelector('.transport-options'), editorEffectiveTransport()
      );
    }
  }
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
  populateTransportSelect(select, data.transports, editorAutoTransportLabel());
  // `''`, not `'http_pull'`: a display that names no transport is delivered
  // over its panel's own (`DisplayConfig.transport_type`,
  // `src/maverick/config.py`), and defaulting this control to a literal would
  // both misreport that and pin it to `http_pull` on the next save.
  const current = String(editorTransport.type || '');
  select.value = current;
  if (current && select.value !== current) {
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
    redrawTransportOptions(
      data, editorDialog.querySelector('.transport-options'), editorEffectiveTransport()
    );
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
  redrawTransportOptions(data, options, editorEffectiveTransport());
  return fields;
}

/** The panel the drawer's panel picker is on, or the display's own before one
 *  has been drawn. */
function editorPanelProfile() {
  const select = editorDialog && editorDialog.querySelector('#editor-panel');
  const id = (select && select.value) || (editorSummary && editorSummary.panel);
  return ((formData && formData.panels) || []).find((p) => p.id === id) || null;
}

/** What the drawer's `automatic` option resolves to, named. */
function editorAutoTransportLabel() {
  const panel = editorPanelProfile();
  return panel
    ? `automatic — ${panel.default_transport} for this panel`
    : 'automatic (from panel)';
}

/** The transport this display would actually be delivered over, which is what
 *  decides the option fields shown under the picker. Mirrors
 *  `DisplayConfig.transport_type` (`src/maverick/config.py`). */
function editorEffectiveTransport() {
  const select = editorDialog && editorDialog.querySelector('#editor-transport-type');
  const chosen = select ? select.value : (editorTransport.type || '');
  if (chosen) return chosen;
  const panel = editorPanelProfile();
  return panel ? panel.default_transport : '';
}

function redrawTransportOptions(data, box, type) {
  const info = (data.schema.transports || {})[type] || { options: {}, fields: {} };
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
  const built = transportControls(info, editorTransport, {
    idPrefix: 'editor-transport',
    environment: (data.environment || {}),
    extras,
    pathPrefix: 'transport.',
    onModeChange: () => {
      editorTransport = Object.assign(withoutShownKeys(), collectTransport());
      redrawTransportOptions(data, box, editorEffectiveTransport());
      markDirty();
    },
  });
  nodes.push(...built.primary);
  if (built.advanced) nodes.push(built.advanced);
  // The same probe the Add dialog offers, on the drawer's unsaved state.
  const row = document.createElement('div');
  row.className = 'probe-row';
  const test = document.createElement('button');
  test.type = 'button';
  test.textContent = 'Test delivery';
  const result = document.createElement('span');
  result.className = 'probe-result';
  result.setAttribute('role', 'status');
  test.addEventListener('click', () => {
    probeCandidate(collectBody(), result, test).then((error) => {
      if (error && error.fields) applyEditorErrors(error.fields, 'The configuration is not valid yet.');
    });
  });
  row.append(test, result);
  nodes.push(row);
  box.replaceChildren(...nodes);
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
  const chosen = select ? select.value : editorTransport.type;
  // No key rather than `type: ""`: both resolve to the panel's default, but
  // only an absent one leaves the saved config saying what the user meant.
  const transport = chosen ? { type: chosen } : {};
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
    now.dataset.framesrc = withToken(
      `api/displays/${encodeURIComponent(summary.id)}/preview.png` +
      `?c=${encodeURIComponent(summary.checksum)}`
    );
    now.dataset.sourcesrc = withToken(
      `api/displays/${encodeURIComponent(summary.id)}/screenshot.png` +
      `?c=${encodeURIComponent(summary.checksum)}`
    );
    now.alt = `current frame for ${summary.name || summary.id}`;
  }
  node.querySelector('.act-preview').addEventListener(
    'click', (e) => runPreview(e.currentTarget)
  );
  node.querySelector('.view-source').addEventListener(
    'click', () => applyEditorPreviewMode(node, 'source')
  );
  node.querySelector('.view-frame').addEventListener(
    'click', () => applyEditorPreviewMode(node, 'frame')
  );
  applyEditorPreviewMode(node, 'frame');
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

/** Point `.compare-now` and `.compare-new` at the source or the frame, from
 *  whichever `data-framesrc`/`data-sourcesrc` each image has so far — the
 *  candidate's only exist once a preview has actually been run. */
function applyEditorPreviewMode(node, mode) {
  node.dataset.viewMode = mode;
  node.querySelector('.view-source').setAttribute('aria-pressed', String(mode === 'source'));
  node.querySelector('.view-frame').setAttribute('aria-pressed', String(mode === 'frame'));
  for (const figure of ['.compare-now', '.compare-new']) {
    const img = node.querySelector(`${figure} img`);
    const src = mode === 'source' ? img.dataset.sourcesrc : img.dataset.framesrc;
    const placeholder = node.querySelector(`${figure} .shot-empty`);
    if (src) {
      img.setAttribute('src', src);
      img.hidden = false;
      placeholder.hidden = true;
    } else {
      img.hidden = true;
      placeholder.hidden = false;
    }
  }
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
  image.dataset.framesrc = 'data:image/png;base64,' + result.preview_png;
  image.dataset.sourcesrc = result.screenshot_png
    ? 'data:image/png;base64,' + result.screenshot_png
    : '';
  image.alt = 'the frame this configuration would produce';
  section.querySelector('.compare').hidden = false;
  section.querySelector('.compare-modes').hidden = false;
  applyEditorPreviewMode(section, section.dataset.viewMode || 'frame');
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

// ------------------------------------------------------ transport controls --

/* One builder for both forms. A transport describes how to ask for each of
 * its options in `option_fields` (`src/maverick/transports/base.py`), served
 * under `fields` by `GET /api/schema/display`: which mode an option belongs
 * to, whether it is required, whether it is advanced, and what control to
 * draw. Before this every option was a text box and every transport showed
 * every one — twelve for an OpenDisplay tag, of which the normal case needs
 * one — and `device_id`, the one it needs, was a value copied out of a URL.
 *
 * Every control carries `data-transport-key` and a string `.value`, which is
 * the contract `buildAddBody` and `collectTransport` read by, so the two forms
 * need to know nothing about what was drawn. */

/** The value a mode option is on, given what has been typed and its default. */
function currentMode(info, values) {
  const key = info.mode_option;
  if (!key) return '';
  const field = (info.fields || {})[key] || {};
  return String(values[key] || field.default || '');
}

/** Build the controls for `info`'s options with `values` filled in.
 *
 *  Returns `{primary, advanced}`: the mode picker and every required or
 *  plain option, and one folded `<details>` holding the advanced ones and
 *  `ctx.extras` (stored keys the transport does not document). `ctx.idPrefix`
 *  keeps element ids unique per form, `ctx.environment` decides which modes
 *  and helpers to offer, `ctx.onModeChange` is called when the picker moves
 *  and is expected to redraw. */
function transportControls(info, values, ctx) {
  const fields = info.fields || {};
  const options = info.options || {};
  const mode = currentMode(info, values);
  const visible = (key) => {
    const field = fields[key] || {};
    return !(field.modes && field.modes.length) || field.modes.includes(mode);
  };
  const primary = [];
  const advanced = [];

  if (info.mode_option && options[info.mode_option] !== undefined) {
    primary.push(modeControl(info, values, ctx));
  }
  const ordered = Object.keys(options).filter((key) => key !== info.mode_option && visible(key));
  const required = ordered.filter((key) => (fields[key] || {}).required);
  const plain = ordered.filter((key) => !(fields[key] || {}).required && !(fields[key] || {}).advanced);
  const folded = ordered.filter((key) => !(fields[key] || {}).required && (fields[key] || {}).advanced);
  for (const key of [...required, ...plain]) {
    primary.push(optionControl(key, options[key], fields[key] || {}, values[key], ctx, mode));
  }
  for (const key of folded) {
    advanced.push(optionControl(key, options[key], fields[key] || {}, values[key], ctx, mode));
  }
  for (const key of ctx.extras || []) {
    advanced.push(optionControl(key, '', { extra: true }, values[key], ctx, mode));
  }

  let fold = null;
  if (advanced.length) {
    fold = document.createElement('details');
    fold.className = 'field-group nested';
    const summary = document.createElement('summary');
    summary.textContent = info.mode_option
      ? `More options for ${MODE_LABELS[mode] || mode}`
      : 'More options';
    fold.append(summary, ...advanced);
    // A stored value under an advanced key is worth seeing without a click.
    if (advanced.some((node) => node.querySelector('[data-transport-key]')?.value)) fold.open = true;
  }
  return { primary, advanced: fold };
}

/** The mode picker: a row of buttons rather than a select, because there are
 *  two or three answers and the choice redraws everything under it. In the
 *  Home Assistant app `ble` is left out — the app has no Bluetooth
 *  (`src/maverick/transports/opendisplay.py`) — unless a stored display is
 *  already on it, in which case it is shown with the refusal spelled out. */
function modeControl(info, values, ctx) {
  const key = info.mode_option;
  const field = info.fields[key] || {};
  const mode = currentMode(info, values);
  const wrap = document.createElement('div');
  wrap.className = 'field';
  wrap.dataset.path = `${ctx.pathPrefix || ''}${key}`;
  const label = document.createElement('label');
  label.textContent = field.label || key;
  const group = document.createElement('div');
  group.className = 'segmented';
  group.setAttribute('role', 'radiogroup');
  group.setAttribute('aria-label', field.label || key);
  const hidden = document.createElement('input');
  hidden.type = 'hidden';
  hidden.dataset.transportKey = key;
  hidden.id = `${ctx.idPrefix}-${key}`;
  // The default is sent as an absent key, so a saved display reads as it
  // was meant: "auto", not "mode: auto" on every line.
  hidden.value = mode === String(field.default || '') ? '' : mode;
  const addon = Boolean(ctx.environment && ctx.environment.addon);
  for (const choice of field.choices || []) {
    if (choice === 'ble' && addon && mode !== 'ble') continue;
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('role', 'radio');
    button.setAttribute('aria-checked', String(choice === mode));
    button.textContent = MODE_LABELS[choice] || choice;
    button.title = MODE_TITLES[choice] || '';
    button.addEventListener('click', () => {
      hidden.value = choice === String(field.default || '') ? '' : choice;
      values[key] = choice;
      ctx.onModeChange();
    });
    group.appendChild(button);
  }
  const help = document.createElement('div');
  help.className = 'help';
  help.textContent = info.options[key] || '';
  wrap.append(label, group, hidden, help);
  if (mode === 'ble' && addon) {
    const warn = document.createElement('div');
    warn.className = 'help warn';
    warn.textContent = 'The Home Assistant app has no Bluetooth of its own, so this mode ' +
      'fails on every delivery here. Switch to Home Assistant, which reaches the tag ' +
      'through its own adapters and ESPHome Bluetooth proxies.';
    wrap.appendChild(warn);
  }
  return wrap;
}

const MODE_LABELS = { auto: 'Automatic', ha: 'Home Assistant', ble: 'This host’s Bluetooth' };
const MODE_TITLES = {
  auto: 'Home Assistant when it is connected, this host’s Bluetooth otherwise',
  ha: 'Through Home Assistant’s Bluetooth and its ESPHome proxies; needs the OpenDisplay integration',
  ble: 'Directly from an adapter on this machine; the tag must be in its range',
};

/** One labelled control for a transport option, drawn by its `kind`. */
function optionControl(key, description, field, value, ctx, mode) {
  const wrap = document.createElement('div');
  wrap.className = 'field';
  wrap.dataset.path = `${ctx.pathPrefix || ''}${key}`;
  const id = `${ctx.idPrefix}-${key}`;
  const label = document.createElement('label');
  label.setAttribute('for', id);
  label.textContent = field.label || key;
  if (field.label && field.label !== key) {
    const code = document.createElement('code');
    code.className = 'option-key';
    code.textContent = key;
    label.append(' ', code);
  }
  if (field.required) {
    const req = document.createElement('span');
    req.className = 'req';
    req.textContent = 'required';
    label.appendChild(req);
  }
  const text = value === undefined || value === null
    ? ''
    : (typeof value === 'object' ? JSON.stringify(value) : String(value));
  let control;
  let extra = null;
  // The device picker sits above its text box, which reads as the fallback
  // it is; every other helper sits below its control.
  let extraFirst = false;
  if (field.kind === 'select') {
    control = document.createElement('select');
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = field.default !== null && field.default !== undefined
      ? `default (${field.default})` : 'unset';
    control.appendChild(blank);
    for (const choice of field.choices || []) {
      const option = document.createElement('option');
      option.value = choice;
      option.textContent = choice;
      control.appendChild(option);
    }
    control.value = text;
    if (text && control.value !== text) {
      const kept = document.createElement('option');
      kept.value = text;
      kept.textContent = text;
      control.appendChild(kept);
      control.value = text;
    }
  } else if (field.kind === 'ha_device') {
    ({ control, extra } = deviceControl(id, text, field, ctx));
    extraFirst = true;
  } else if (field.kind === 'mac') {
    ({ control, extra } = macControl(id, text, ctx, mode));
  } else {
    control = document.createElement('input');
    control.type = field.kind === 'number' ? 'number' : field.kind === 'secret' ? 'password' : 'text';
    if (field.kind === 'number') control.step = 'any';
    control.autocomplete = 'off';
    control.value = text;
    if (field.default !== null && field.default !== undefined) control.placeholder = String(field.default);
  }
  control.id = id;
  control.dataset.transportKey = key;
  const help = document.createElement('div');
  help.className = 'help' + (field.extra ? ' warn' : '');
  help.textContent = field.extra
    ? 'Not an option this transport documents. It is kept as it is; clear the box to drop it.'
    : description;
  const error = document.createElement('div');
  error.className = 'field-error';
  wrap.appendChild(label);
  if (extra && extraFirst) wrap.appendChild(extra);
  wrap.appendChild(control);
  if (extra && !extraFirst) wrap.appendChild(extra);
  wrap.append(help, error);
  return wrap;
}

async function ensureDevices(refresh) {
  if (devicesCache && !refresh) return devicesCache;
  try {
    const response = await authFetch('api/ha/opendisplay/devices');
    devicesCache = await response.json();
  } catch (error) {
    devicesCache = [];
    devicesCache.unavailable = error.message;
  }
  return devicesCache;
}

/** A picker over the device registry, writing the registry id into the text
 *  box beside it. The box stays: an id can still be pasted, and a stored one
 *  the registry no longer lists is shown rather than dropped. */
function deviceControl(id, value, field, ctx) {
  const control = document.createElement('input');
  control.type = 'text';
  control.autocomplete = 'off';
  control.spellcheck = false;
  control.value = value;
  control.placeholder = 'device registry id';
  control.className = 'device-id';

  const row = document.createElement('div');
  row.className = 'device-row';
  const select = document.createElement('select');
  select.className = 'device-select';
  select.setAttribute('aria-label', 'Tag known to Home Assistant');
  const refresh = document.createElement('button');
  refresh.type = 'button';
  refresh.className = 'device-refresh';
  refresh.textContent = '↻';
  refresh.title = 'Ask Home Assistant again';
  refresh.setAttribute('aria-label', 'Refresh the list of tags');
  row.append(select, refresh);
  const note = document.createElement('div');
  note.className = 'help device-note';

  const fill = (devices) => {
    select.replaceChildren();
    const head = document.createElement('option');
    head.value = '';
    if (devices.unavailable) {
      head.textContent = 'Home Assistant is not connected — paste the id below';
      select.disabled = true;
    } else if (!devices.length) {
      head.textContent = 'No OpenDisplay tags in Home Assistant yet';
      select.disabled = true;
    } else {
      head.textContent = 'Pick a tag…';
      select.disabled = false;
    }
    select.appendChild(head);
    for (const device of devices) {
      const option = document.createElement('option');
      option.value = device.id;
      const bits = [device.name];
      if (device.model) bits.push(device.model);
      if (device.display_id) bits.push(`already ${device.display_id}`);
      option.textContent = bits.join(' · ');
      option.dataset.panel = device.panel_guess || '';
      select.appendChild(option);
    }
    select.value = control.value;
    if (control.value && select.value !== control.value) select.value = '';
    note.textContent = devices.unavailable
      ? 'The picker needs Home Assistant; the id is on the tag’s device page, at the ' +
        'end of its URL.'
      : (devices.length ? '' : 'Set up the OpenDisplay integration and the tag appears here.');
  };
  select.addEventListener('change', () => {
    if (!select.value) return;
    control.value = select.value;
    control.dispatchEvent(new Event('input', { bubbles: true }));
    control.dispatchEvent(new Event('change', { bubbles: true }));
    // A tag names its size; a panel picker still on the first entry is
    // almost certainly not the right one, so offer the guess.
    const guess = select.selectedOptions[0]?.dataset.panel;
    if (guess && ctx.onPanelGuess) ctx.onPanelGuess(guess);
  });
  control.addEventListener('input', () => {
    select.value = control.value;
    if (select.value !== control.value) select.value = '';
  });
  refresh.addEventListener('click', () => act(refresh, async () => fill(await ensureDevices(true))));
  const placeholder = document.createElement('option');
  placeholder.textContent = 'Loading tags…';
  select.appendChild(placeholder);
  ensureDevices(false).then(fill);

  const extra = document.createElement('div');
  extra.append(row, note);
  return { control, extra };
}

/** A MAC box with a scan button when this host can scan
 *  (`environment.bluetooth_scan`): a scan lists the tags the adapter hears,
 *  and picking one fills the MAC. In the app there is no adapter and no
 *  button, and the help says so. */
function macControl(id, value, ctx, mode) {
  const control = document.createElement('input');
  control.type = 'text';
  control.autocomplete = 'off';
  control.spellcheck = false;
  control.value = value;
  control.placeholder = 'AA:BB:CC:DD:EE:FF';
  const extra = document.createElement('div');
  const environment = ctx.environment || {};
  if (environment.bluetooth_scan) {
    const row = document.createElement('div');
    row.className = 'device-row';
    const scan = document.createElement('button');
    scan.type = 'button';
    scan.textContent = 'Scan for tags';
    const select = document.createElement('select');
    select.className = 'device-select';
    select.hidden = true;
    select.setAttribute('aria-label', 'Tags in range');
    const status = document.createElement('span');
    status.className = 'probe-result';
    row.append(scan, select, status);
    scan.addEventListener('click', async () => {
      setBusy(scan, true);
      status.textContent = 'scanning… 10 s';
      status.className = 'probe-result';
      try {
        const response = await authFetch('api/opendisplay/scan?timeout=10', { method: 'POST' });
        const body = await response.json();
        const tags = body.tags || [];
        select.replaceChildren();
        const head = document.createElement('option');
        head.value = '';
        head.textContent = tags.length ? 'Pick a tag…' : 'No tags heard';
        select.appendChild(head);
        for (const tag of tags) {
          const option = document.createElement('option');
          option.value = tag.mac;
          option.textContent = `${tag.name} · ${tag.mac}`;
          option.dataset.name = tag.name;
          option.dataset.panel = tag.panel_guess || '';
          select.appendChild(option);
        }
        select.hidden = false;
        status.textContent = `${tags.length} tag${tags.length === 1 ? '' : 's'} in range`;
      } catch (error) {
        status.textContent = error.message;
        status.className = 'probe-result err';
      } finally {
        setBusy(scan, false);
      }
    });
    select.addEventListener('change', () => {
      if (!select.value) return;
      control.value = select.value;
      control.dispatchEvent(new Event('input', { bubbles: true }));
      control.dispatchEvent(new Event('change', { bubbles: true }));
      const guess = select.selectedOptions[0]?.dataset.panel;
      if (guess && ctx.onPanelGuess) ctx.onPanelGuess(guess);
    });
    extra.appendChild(row);
  } else if (environment.addon) {
    const note = document.createElement('div');
    note.className = 'help';
    note.textContent = 'No scan here: the app has no Bluetooth. Home Assistant’s own ' +
      'OpenDisplay integration finds tags; pick Home Assistant as the mode.';
    extra.appendChild(note);
  } else if (mode === 'ble' || mode === 'auto') {
    const note = document.createElement('div');
    note.className = 'help';
    note.textContent = 'Install the opendisplay extra to scan from here, or run ' +
      '`maverick scan` on a machine with an adapter.';
    extra.appendChild(note);
  }
  return { control, extra: extra.childNodes.length ? extra : null };
}

// ---------------------------------------------------------- test delivery --

/** `POST /api/displays/probe` with a candidate body; paint the verdict into
 *  `target`. Resolves to the error for the caller to put under fields, or
 *  null. Nothing is saved and no frame is sent (`Engine.probe_candidate`). */
async function probeCandidate(body, target, button) {
  probeResult(target, { busy: true });
  setBusy(button, true);
  try {
    const result = await sendJSON('api/displays/probe', 'POST', body, 'The probe failed.');
    probeResult(target, result);
    return null;
  } catch (error) {
    if (error.unauthorised) {
      probeResult(target, null);
      return error;
    }
    probeResult(target, { ok: false, detail: error.message });
    return error;
  } finally {
    setBusy(button, false);
  }
}

function probeResult(target, result) {
  if (!target) return;
  target.replaceChildren();
  target.className = 'probe-result';
  if (!result) return;
  if (result.busy) {
    target.textContent = 'asking…';
    return;
  }
  const pill = document.createElement('span');
  pill.className = 'pill ' + (result.ok ? 'ok' : 'err');
  pill.textContent = result.ok ? (result.pending ? 'ready' : 'reachable') : 'not reachable';
  target.append(pill, ' ' + (result.detail || ''));
  target.classList.add(result.ok ? 'ok' : 'err');
  // The row sits just above the dialog's sticky action bar, which would
  // otherwise cover the verdict the click was for.
  target.scrollIntoView({ block: 'nearest', behavior: 'auto' });
}

// --------------------------------------------------------- install on device --

/* The ESPHome hand-off, as steps rather than a link to a YAML document.
 * `GET /api/displays/{id}/esphome` (`src/maverick/server/api.py`) carries the
 * document, the node name, the secrets it references — with the value of
 * Maverick's own token — where it can be written so the ESPHome Device
 * Builder sees it, and the Device Builder's own page. Maverick does not
 * compile or flash: the Device Builder does both, and this gets the file and
 * its secrets in front of it with nothing to retype. */

async function loadInstall(id, card) {
  const steps = card.querySelector('.install-steps');
  const error = card.querySelector('.install-error');
  error.hidden = true;
  steps.replaceChildren(muted('Generating…'));
  try {
    const response = await authFetch(`api/displays/${encodeURIComponent(id)}/esphome`);
    renderInstall(card, id, await response.json());
  } catch (e) {
    steps.replaceChildren();
    if (!e.unauthorised) {
      error.textContent = 'Could not generate the configuration: ' + e.message;
      error.hidden = false;
    }
  }
}

function muted(text) {
  const node = document.createElement('p');
  node.className = 'meta';
  node.textContent = text;
  return node;
}

function renderInstall(card, id, info) {
  const steps = card.querySelector('.install-steps');
  const nodes = [];

  if (!info.model_known) {
    nodes.push(warnBox(
      'ESPHome has no driver for this panel in Maverick’s catalogue. The file names ' +
      'a placeholder model; set `model:` (and `platform:`, if the panel is not a ' +
      'waveshare_epaper one) before installing, or the screen stays blank.'
    ));
  }
  if (info.needs_psram) {
    nodes.push(warnBox(
      `This frame needs about ${Math.round(info.decoded_bytes / 1024)} KB of RAM to decode, ` +
      'more than a plain ESP32 has. The file asks for PSRAM; use a board that has it ' +
      `(the config is for “${info.board}”) or set esphome.board under Edit.`
    ));
  }

  const list = document.createElement('ol');
  list.className = 'steps';

  // 1. secrets
  const secretsText = secretsSnippet(info.secrets);
  const secretsPre = pre(secretsText, 'install-pre install-secrets');
  const copySecrets = actionButton('Copy', () => copyText(secretsPre, copySecrets));
  list.appendChild(step(
    'Secrets',
    'ESPHome resolves every !secret from the secrets.yaml beside the configuration. ' +
    'Add these names there; Maverick fills in the one it knows.',
    [row(copySecrets), secretsPre]
  ));

  // 2. the file
  const yamlPre = pre(info.yaml, 'install-pre install-yaml');
  const copyYaml = actionButton('Copy', () => copyText(yamlPre, copyYaml));
  const download = document.createElement('a');
  download.textContent = 'Download';
  download.className = 'install-download';
  download.setAttribute('download', info.filename);
  // A blob rather than the route: an <a download> cannot send the token.
  download.href = URL.createObjectURL(new Blob([info.yaml], { type: 'text/yaml' }));
  const fileRow = row(copyYaml, download);
  const sendRow = sendToEsphome(id, info);
  list.appendChild(step(
    `The configuration, ${info.filename}`,
    sendRow
      ? 'Send it straight to the ESPHome Device Builder’s folder, or copy it into a new ' +
        'device there yourself.'
      : 'Copy it into a new device in the ESPHome Device Builder, or save it beside your ' +
        'other ESPHome configurations.',
    sendRow ? [fileRow, sendRow, yamlPre] : [fileRow, yamlPre]
  ));

  // 3. install
  const installNodes = [];
  if (info.dashboard) {
    const open = document.createElement('a');
    open.className = 'install-open';
    open.textContent = `Open ${info.dashboard.name}`;
    open.href = info.dashboard.url;
    // Out of the ingress iframe: the Device Builder is another add-on's page.
    open.target = '_top';
    open.rel = 'noopener';
    installNodes.push(row(open));
  }
  const method = document.createElement('p');
  method.className = 'meta';
  method.textContent = info.dashboard
    ? `In the Device Builder, ${info.node} appears as a device. Choose Install, then either ` +
      'plug the board into this computer or install wirelessly once it is on the network.'
    : `With the ESPHome CLI: esphome run ${info.filename}. The ESPHome Device Builder add-on ` +
      'does the same from a browser, and this step links to it once it is installed.';
  installNodes.push(method);
  if (info.deep_sleep) {
    installNodes.push(muted(
      'Battery mode: the node sleeps between fetches and is unreachable while it does, so a ' +
      'later update over the air needs the reset button pressed first.'
    ));
  }
  list.appendChild(step('Install', '', installNodes));

  nodes.push(list);
  steps.replaceChildren(...nodes);
}

/** `secrets.yaml` as the file expects it, values filled in where Maverick has them. */
function secretsSnippet(secrets) {
  const lines = ['# secrets.yaml, next to the ESPHome configuration'];
  for (const entry of secrets) {
    if (entry.value) lines.push(`${entry.name}: ${JSON.stringify(entry.value)}`);
    else lines.push(`${entry.name}: ""   # ${entry.description}`);
  }
  return lines.join('\n') + '\n';
}

/** The "Send to ESPHome" row, or null when nowhere on this host qualifies
 *  (`destinations` in `src/maverick/esphome/install.py`). One writable
 *  destination is a button; several are a picker and a button. */
function sendToEsphome(id, info) {
  const targets = (info.destinations || []).filter((d) => d.writable || d.kind === 'share');
  if (!targets.length) return null;
  const wrap = document.createElement('div');
  wrap.className = 'install-send';
  const line = document.createElement('div');
  line.className = 'row';
  let picker = null;
  if (targets.length > 1) {
    picker = document.createElement('select');
    picker.setAttribute('aria-label', 'Where to write the configuration');
    for (const target of targets) {
      const option = document.createElement('option');
      option.value = target.id;
      option.textContent = `${target.label} (${target.path})`;
      picker.appendChild(option);
    }
    line.appendChild(picker);
  }
  const send = actionButton('Send to ESPHome', () => submit(false));
  send.classList.add('add-btn');
  line.appendChild(send);
  const status = document.createElement('div');
  status.className = 'meta install-status';
  wrap.append(line, status);

  const current = () => targets.find((t) => t.id === (picker ? picker.value : targets[0].id));
  const describe = () => {
    const target = current();
    if (!target) return;
    if (target.installed) {
      status.textContent = `${info.filename} is already in ${target.label}. Sending again ` +
        'replaces it with this version.';
    } else {
      status.textContent = `Writes ${info.filename} into ${target.path}.`;
    }
    status.append(secretsNote(target.missing_secrets, info.secrets));
  };
  if (picker) picker.addEventListener('change', describe);
  describe();

  async function submit(overwrite) {
    const target = current();
    setBusy(send, true);
    status.textContent = 'Writing…';
    try {
      const result = await sendJSON(
        `api/displays/${encodeURIComponent(id)}/esphome/install`, 'POST',
        { destination: target.id, overwrite: overwrite }, 'The configuration could not be written.'
      );
      status.textContent = `Written to ${result.path}. `;
      status.append(secretsNote(result.missing_secrets, info.secrets));
      if (result.dashboard) {
        status.append(' Open ', link(result.dashboard.name, result.dashboard.url), ' and install it.');
      }
      target.installed = true;
    } catch (error) {
      if (error.status === 409) {
        status.textContent = error.message + ' ';
        const replace = actionButton('Replace it', () => submit(true));
        status.appendChild(replace);
      } else if (!error.unauthorised) {
        status.textContent = error.message;
      }
    } finally {
      setBusy(send, false);
    }
  }
  return wrap;
}

/** What the destination's secrets.yaml still lacks, as a fragment. */
function secretsNote(missing, secrets) {
  const fragment = document.createDocumentFragment();
  if (missing === null || missing === undefined) {
    fragment.append(' There is no secrets.yaml there yet; step 1 is its contents.');
  } else if (missing.length) {
    fragment.append(` Its secrets.yaml is missing ${missing.join(', ')}; add ${
      missing.length === 1 ? 'it' : 'them'} from step 1.`);
  } else {
    fragment.append(' Its secrets.yaml already has every name the file needs.');
  }
  return fragment;
}

function step(title, lead, children) {
  const item = document.createElement('li');
  const head = document.createElement('div');
  head.className = 'step-title';
  head.textContent = title;
  item.appendChild(head);
  if (lead) {
    const text = document.createElement('p');
    text.className = 'meta';
    text.textContent = lead;
    item.appendChild(text);
  }
  item.append(...children);
  return item;
}

function warnBox(text) {
  const node = document.createElement('div');
  node.className = 'install-warn';
  node.textContent = text;
  return node;
}

function pre(text, className) {
  const node = document.createElement('pre');
  node.className = className;
  node.tabIndex = 0;
  node.textContent = text;
  return node;
}

function row(...children) {
  const node = document.createElement('div');
  node.className = 'row';
  node.append(...children);
  return node;
}

function link(text, href) {
  const node = document.createElement('a');
  node.textContent = text;
  node.href = href;
  node.target = '_top';
  node.rel = 'noopener';
  return node;
}

function actionButton(label, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = label;
  button.addEventListener('click', onClick);
  return button;
}

// ----------------------------------------------------------- discovered tags --

/* The tags Home Assistant's OpenDisplay integration has found, offered as
 * displays to add with the panel, the transport and the device id filled in.
 * This is the TRMNL shape of onboarding — the device is known before the
 * display exists — applied to BLE tags, where the device registry is what
 * knows them. The section stays hidden until there is something to show. */

async function loadDiscovery() {
  const section = document.getElementById('discovery');
  if (!section) return;
  const devices = await ensureDevices(true);
  const list = document.getElementById('discovery-list');
  if (!devices.length) {
    section.hidden = true;
    return;
  }
  // The catalogue, so a guessed panel can be named rather than shown as an id.
  try { await ensureFormData(); } catch (error) { /* the id will do */ }
  list.replaceChildren(...devices.map(tagCard));
  section.hidden = false;
}

function tagCard(device) {
  const card = document.createElement('div');
  card.className = 'tag-card' + (device.display_id ? ' is-added' : '');
  const name = document.createElement('div');
  name.className = 'tag-name';
  name.textContent = device.name;
  const meta = document.createElement('div');
  meta.className = 'tag-meta';
  meta.textContent = [device.manufacturer, device.model].filter(Boolean).join(' · ') || 'OpenDisplay tag';
  const guess = document.createElement('div');
  guess.className = 'tag-guess';
  const panel = ((formData && formData.panels) || []).find((p) => p.id === device.panel_guess);
  guess.textContent = device.panel_guess
    ? `Looks like ${panel ? panel.name : device.panel_guess}`
    : 'Panel not recognised from the model — pick it when adding';
  card.append(name, meta, guess);
  if (device.display_id) {
    const added = actionButton(`Added as ${device.display_id}`, () => {
      const target = cards.get(device.display_id);
      if (target) target.scrollIntoView({ block: 'center', behavior: 'smooth' });
    });
    added.className = 'tag-added';
    card.appendChild(added);
  } else {
    const add = actionButton('Add as display', (e) => openAddDialog(e.currentTarget, {
      name: device.name,
      panel: device.panel_guess,
      transport: { type: 'opendisplay', mode: 'ha', device_id: device.id },
    }));
    add.className = 'add-btn';
    card.appendChild(add);
  }
  return card;
}
