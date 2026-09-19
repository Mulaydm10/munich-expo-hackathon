/* Control-room wiring. All data via ./app-api.js; drawing via ./app-chart.js. */
import * as api from './app-api.js';
import { drawMain, drawPrice, drawPooling, drawHeartbeat } from './app-chart.js';
import { NetworkMap } from './app-map.js';
import { DispatchFlow } from './app-dispatch.js';
import { Copilot } from './app-copilot.js';
import { runAutopilot } from './app-story.js';
import { el, clear } from './app-dom.js';

const $ = (id) => document.getElementById(id);
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

const app = {
  rows: [], scn: null, mapRows: [], assumptions: [], selectedSite: null,
  cursor: 0, playing: false, speed: 2, reveal: 1,
  toggles: { env: false, band: true, firm: false, xray: false, compare: false },
  dispatchResult: null, dispatchEvent: null, story: null, twin: null,
};
window.flexgrid = app; // handy for the demo console

/* ---------- boot ---------- */
(async function boot() {
  const [health, scn] = await Promise.all([
    api.getHealth(), api.getScenario(),
  ]);
  const [ts, mapBody, pool, assumptions] = await Promise.all([
    api.getTimeseries(scn.id), api.getMap(scn.id), api.getPooling(scn.id), api.getAssumptions(),
  ]);
  const mapRows = mapBody.sites;
  app.scn = scn; app.rows = ts.rows; app.mapRows = mapRows; app.assumptions = assumptions;

  $('m-date').textContent = api.berlinDate(ts.rows[0].t);
  $('m-sites').textContent = `${scn.spec.n_sites ?? api.MISSING} · ${scn.spec.region || 'national'}`;
  $('m-status').textContent = scn.cached ? 'cached' : scn.status;
  $('m-health').textContent = health.ok ? `ok (${health.backend})` : 'unavailable';
  $('site-count').textContent = `${mapRows.length} sites`;

  fillScenario(scn);
  renderWarnings(scn.warnings);
  renderAssumptions(assumptions);
  renderSiteList(mapRows.slice(0, 14));
  app.poolPoints = pool.points;
  if (!app.poolPoints.length && pool.warnings.length) {
    $('p-pooling').appendChild(el('p', { className: 'warn-item', text: `${pool.warnings[0].message || 'Pooling data unavailable'} — ${api.MISSING}` }));
  }
  drawPooling($('pooling'), pool.points);

  // network map
  app.map = new NetworkMap($('map'), { onSelect: (s) => selectSite(s) });
  app.map.setData(mapRows);
  $('v-de').addEventListener('click', () => setMapZoom('de'));
  $('v-muc').addEventListener('click', () => setMapZoom('muc'));

  // cursor range
  const cur = $('cursor');
  cur.max = String(app.rows.length - 1);
  cur.addEventListener('input', () => { setCursor(+cur.value); play(false); });

  // peak cursor default: the baseline peak interval
  app.peakIdx = app.rows.reduce((b, r, i) => (r.load_kw_baseline != null && r.load_kw_baseline > (app.rows[b].load_kw_baseline ?? -1) ? i : b), 0);
  setCursor(app.peakIdx);

  // mini digital twin
  try {
    const mod = await import('./app-scene.js');
    if (mod.webglAvailable()) app.twin = new mod.DepotScene($('twin'), { mode: 'mini', orbit: true });
  } catch (e) { console.warn('twin unavailable', e.message); }

  app.dispatchFlow = new DispatchFlow(app);
  app.dispatchFlow.runWith = async (prefill) => { app.dispatchFlow.open(prefill); return app.dispatchFlow.run(); };
  app.copilot = new Copilot(app);

  wireUI();
  // cross-page transition: the landing load pulse becomes the baseline line
  const handoff = sessionStorage.getItem('flexgrid_handoff');
  if (handoff && !reduced && Date.now() - JSON.parse(handoff).at < 60000) {
    sessionStorage.removeItem('flexgrid_handoff');
    app.reveal = 0;
    const t0 = performance.now();
    const grow = () => {
      app.reveal = Math.min(1, (performance.now() - t0) / 1100);
      if (app.reveal < 1) requestAnimationFrame(grow);
    };
    grow();
  }
  frame();
  // some embedding contexts throttle rAF; keep the control room drawing anyway
  setTimeout(() => {
    if (frames < 2) { app.timerLoop = true; setInterval(() => frame(performance.now()), 33); }
  }, 1200);
})();

/* ---------- panels ---------- */
function fillScenario(scn) {
  const fa = scn.forecast_accuracy, sc = scn.scorecard, tt = scn.totals;
  const set = (id, v) => { $(id).textContent = v; };
  set('k-base', api.fmt(tt.peak_kw_baseline, 'kW'));
  set('k-opt', api.fmt(tt.peak_kw_optimised, 'kW'));
  const delta = tt.peak_kw_baseline == null || tt.peak_kw_optimised == null
    ? null : tt.peak_kw_baseline - tt.peak_kw_optimised;
  set('k-delta', api.fmt(delta, 'kW'));
  set('fa-acc', api.fmt(fa.accuracy_pct, '%'));
  set('fa-wape', api.fmt(fa.wape, '', 3));
  set('fa-snaive', api.fmt(fa.seasonal_naive_wape, '', 3));
  set('fa-clim', api.fmt(fa.climatology_wape, '', 3));
  set('fa-cov', api.fmt(fa.coverage_q05_q95, '', 2));
  $('fa-sharp').textContent = api.fmt(fa.sharpness_kw, 'kW');
  $('fa-hist').textContent = api.fmt(fa.history_days, 'days');
  $('sc-miss').textContent = api.fmt(sc.deadline_misses);
  $('sc-unmet').textContent = api.fmt(sc.unmet_energy_kwh, 'kWh', 1);
  $('sc-env').textContent = api.fmt(sc.envelope_violations, 'kWh', 1);
  $('sc-inf').textContent = api.fmt(sc.infeasible_sites);
  $('sc-veh').textContent = api.fmt(sc.vehicles_scheduled);
  $('tt-cost').textContent = api.fmt(tt.energy_cost_eur, '€', 2);
  nullSafe($('tt-rev'), tt.capacity_revenue_eur, '€', 'no balancing-market source');
  nullSafe($('tt-net'), tt.net_eur, '€', 'depends on capacity revenue');
  $('tt-co2').textContent = api.fmt(tt.co2_kg_saved, 'kg', 1);
  $('tt-firm').textContent = api.fmt(tt.pool_firm_mw, 'MW', 2);
  $('warn-count').textContent = scn.warnings.length;
  $('w-count').textContent = scn.warnings.length;
}

function nullSafe(el, value, unit, why) {
  if (value === null || value === undefined) {
    el.className = 'v missing';
    el.textContent = `not available (${why})`;
    el.style.fontSize = '14px';
  } else { el.className = 'v'; el.textContent = api.fmt(value, unit, 2); }
}

function renderWarnings(warnings) {
  const list = $('warn-list');
  clear(list);
  warnings.forEach((w) => list.appendChild(el('div', { className: 'warn-item' },
    el('span', { className: `tag ${api.warningIsInfo(w) ? 'info' : 'warn'}`, text: api.warningIsInfo(w) ? 'info' : 'warning' }),
    el('span', { className: 'code', text: ` ${w.code || ''}` }),
    el('div', { text: w.message || '' }),
    el('div', { className: 'faint mono', text: w.lane || '' }))));
}

function renderAssumptions(list) {
  const target = $('assume-list');
  clear(target);
  list.forEach((a) => target.appendChild(el('div', { className: 'warn-item' },
    el('div', { className: 'kv' },
      el('span', { className: 'k mono', text: a.key }),
      el('span', { className: a.value == null ? 'v missing' : 'v', text: a.value == null ? api.MISSING : `${a.value}${a.unit ? ` ${a.unit}` : ''}` })),
    el('div', { className: 'faint', text: `${a.source || 'no source connected'} — ${a.note || ''}` }))));
}

function renderSiteList(rows) {
  const target = $('site-rows');
  clear(target);
  rows.forEach((s) => {
    const tr = el('tr', { className: app.selectedSite?.site_id === s.site_id ? 'sel' : '' },
      el('td', { className: 'mono', text: String(s.site_id || '').replace('DE-MUC-', '') }),
      el('td', { className: s.rated_power_kw == null ? 'missing' : '', text: s.rated_power_kw == null ? api.MISSING : api.fmt(s.rated_power_kw, 'kW') }),
      el('td', { text: api.fmt(s.peak_kw_optimised, 'kW', 1) }),
      el('td', { text: api.fmt(s.firm_kw, 'kW', 1) }),
      el('td', { text: '' }));
    tr.dataset.id = s.site_id;
    target.appendChild(tr);
  });
  [...target.querySelectorAll('tr')].forEach((tr) => tr.addEventListener('click', () => {
    const s = app.mapRows.find((x) => x.site_id === tr.dataset.id);
    selectSite(s);
  }));
}

function selectSite(s) {
  app.selectedSite = s;
  app.map.select(s.site_id);
  renderSiteList(app.mapRows.slice(0, 14).includes(s) ? app.mapRows.slice(0, 14) : [s, ...app.mapRows.slice(0, 13)]);
}

function setMapZoom(v) {
  app.map.setView(v);
  $('v-de').setAttribute('aria-pressed', String(v === 'de'));
  $('v-muc').setAttribute('aria-pressed', String(v === 'muc'));
}

/* ---------- playback + drawing ---------- */
function setCursor(i) {
  app.cursor = Math.max(0, Math.min(app.rows.length - 1, i));
  const row = app.rows[app.cursor];
  $('cursor').value = String(app.cursor);
  $('cursor-time').textContent = api.berlin(row.t);
  if (app.twin) {
    const v = row.load_kw_optimised;
    app.twin.setLoad(v == null ? 0.1 : v / row.envelope_kw, { phase: app.dispatchResult ? 'optimised' : 'optimised' });
    const hour = Number(api.berlin(row.t, { hour: '2-digit', hour12: false }));
    app.twin.setTime(hour < 12 ? hour + 24 : hour);
    app.twin.setReady(hour >= 5 && hour < 12 ? 4 : hour >= 2 ? 2 : 0);
    $('twin-time').textContent = api.berlin(row.t);
  }
}

function play(on) {
  app.playing = on === undefined ? !app.playing : on;
  $('btn-play').textContent = app.playing ? 'Pause (space)' : 'Play (space)';
}

let last = 0, frames = 0;
function frame(now = 0) {
  frames++;
  if (!app.timerLoop) requestAnimationFrame(frame);
  if (app.playing && now - last > 380 / app.speed) {
    last = now;
    setCursor((app.cursor + 1) % app.rows.length);
  }
  const shown = app.reveal >= 1 ? app.rows : app.rows.slice(0, Math.max(2, Math.round(app.rows.length * app.reveal)));
  drawMain($('chart'), {
    rows: shown, cursor: app.reveal >= 1 ? app.cursor : null,
    showEnvelope: app.toggles.env, showBand: app.toggles.band, showFirm: app.toggles.firm,
    xray: app.toggles.xray, dispatch: app.dispatchResult,
  });
  drawPrice($('price'), app.rows, app.cursor);
  if (app.poolPoints) drawPooling($('pooling'), app.poolPoints);
  if (!reduced) drawHeartbeat($('heartbeat'), app.rows, app.cursor, !!app.dispatchResult || app.toggles.compare === false);
  $('m-clock').textContent = new Date().toLocaleTimeString('en-GB', { timeZone: 'Europe/Berlin' });
}

/* ---------- UI wiring ---------- */
function wireUI() {
  $('btn-play').addEventListener('click', () => play());
  $('speed').addEventListener('change', (e) => { app.speed = Number(e.target.value); });

  const toggle = (id, key, label) => $(id).addEventListener('click', () => {
    app.toggles[key] = !app.toggles[key];
    $(id).setAttribute('aria-pressed', String(app.toggles[key]));
    if (key === 'xray' && app.twin) app.twin.setPeakWall(app.toggles.xray ? 0.8 : 0);
    if (label) $(id).textContent = label(app.toggles[key]);
  });
  toggle('t-env', 'env'); toggle('t-band', 'band'); toggle('t-firm', 'firm'); toggle('t-xray', 'xray');
  $('t-compare').addEventListener('click', () => {
    app.toggles.compare = !app.toggles.compare;
    $('t-compare').setAttribute('aria-pressed', String(app.toggles.compare));
    // compare = short reveal of baseline only vs optimised only
    app.toggles.band = !app.toggles.compare;
    $('t-band').setAttribute('aria-pressed', String(app.toggles.band));
  });

  $('btn-dispatch').addEventListener('click', () => app.dispatchFlow.open({ call_t: new Date(app.rows[app.peakIdx].t).toISOString() }));
  $('btn-scenario').addEventListener('click', () => $('scenario-dlg').showModal());
  $('scn-cancel').addEventListener('click', () => $('scenario-dlg').close());
  $('scn-run').addEventListener('click', runScenario);

  $('btn-warn').addEventListener('click', () => $('p-warnings').scrollTop = 0);

  // modes
  const setMode = (story) => {
    $('mode-story').setAttribute('aria-pressed', String(story));
    $('mode-op').setAttribute('aria-pressed', String(!story));
    document.body.classList.toggle('story-mode', story);
    if (story) { app.story = runAutopilot(publicApi); } else if (app.story) { app.story.stop(); app.story = null; }
  };
  $('mode-story').addEventListener('click', () => setMode(true));
  $('mode-op').addEventListener('click', () => setMode(false));
  $('run-story').addEventListener('click', () => {
    if (app.story) { app.story.stop(); app.story = null; $('run-story').textContent = 'Run the story'; return; }
    app.story = runAutopilot(publicApi);
    $('run-story').textContent = 'Stop story';
  });

  // proof drawer
  document.body.addEventListener('click', (e) => {
    const b = e.target.closest('.src');
    if (!b) return;
    const field = b.dataset.src;
    $('proof-field').textContent = field;
    const value = field.split('.').reduce((o, k) => (o ? o[k] : undefined), app.scn);
    const body = $('proof-body');
    clear(body);
    body.appendChild(el('div', { className: 'rowlist' },
      el('div', { className: 'kv' }, el('span', { className: 'k', text: 'API field' }), el('span', { className: 'v mono', text: field })),
      el('div', { className: 'kv' }, el('span', { className: 'k', text: 'Returned by' }), el('span', { className: 'v mono', text: `GET /api/scenario/${app.scn.id}` }))));
    body.appendChild(el('pre', { className: 'mono', text: JSON.stringify(value === undefined ? { note: 'see endpoint' } : value, null, 2) }));
    body.appendChild(el('p', { className: 'dim', text: 'Charging sessions behind this figure are synthesized; grid load, weather, price and charging locations are real public data.' }));
    $('proof-dlg').showModal();
  });
  $('proof-close').addEventListener('click', () => $('proof-dlg').close());

  addEventListener('keydown', (e) => {
    if (e.target.matches('input, select, textarea')) return;
    if (e.code === 'Space') { e.preventDefault(); play(); }
    if (e.key === 'd') { e.preventDefault(); app.dispatchFlow.open({ call_t: new Date(app.rows[app.peakIdx].t).toISOString() }); }
    if (e.key === 'ArrowRight') setCursor(app.cursor + 1);
    if (e.key === 'ArrowLeft') setCursor(app.cursor - 1);
  });

  if (location.hash === '#evidence') $('evidence').scrollTop = 0;
}

async function runScenario() {
  const dlg = $('scenario-dlg');
  const v = Object.fromEntries([...dlg.querySelectorAll('input, select')].map((i) => [i.name, i.value]));
  const spec = { date: v.date, seed: Number(v.seed), product: v.product, tau: Number(v.tau), policy: v.policy, pool_method: v.pool_method };
  if (v.selector === 'n_sites') spec.n_sites = Number(v.selector_value);
  else if (v.selector === 'region') spec.region = v.selector_value;
  else spec.site_ids = v.selector_value.split(',').map((s) => s.trim()).filter(Boolean);

  const err = $('scn-error');
  err.style.display = 'none';
  if (v.selector === 'n_sites' && (!spec.n_sites || spec.n_sites < 1)) {
    err.style.display = 'block';
    err.textContent = 'Validation error: n_sites must be ≥ 1. Provide exactly one of n_sites, region, site_ids.';
    return;
  }
  $('scn-progress').style.display = 'block';
  $('m-status').textContent = 'running';
  try {
    const res = await api.createScenario(spec, ({ progress }) => {
      $('scn-bar').value = progress;
      $('scn-pct').textContent = Math.round(progress * 100);
    });
    app.scn = res;
    fillScenario(res);
    renderWarnings(res.warnings);
    $('m-status').textContent = res.cached ? 'cached' : 'complete';
    location.hash = `scenario=${res.id}`;
    dlg.close();
  } catch (e) {
    err.style.display = 'block';
    err.textContent = `Scenario failed: ${e.message}. Retry or change inputs.`;
    $('m-status').textContent = 'failed';
  } finally {
    $('scn-progress').style.display = 'none';
  }
}

/* ---------- surface used by the autopilot / copilot ---------- */
const publicApi = {
  get scn() { return app.scn; },
  get twin() { return app.twin; },
  get dispatchFlow() { return app.dispatchFlow; },
  setMapZoom,
  play,
  goToPeak() { setCursor(app.peakIdx); play(false); },
  highlight(what) {
    const el = { forecast: 'p-forecast', pooling: 'p-pooling', evidence: 'evidence' }[what];
    if (!el) return;
    const node = $(el);
    node.style.transition = 'box-shadow 400ms';
    node.style.boxShadow = '0 0 0 2px var(--cyan)';
    setTimeout(() => { node.style.boxShadow = 'none'; }, 3400);
  },
  openDispatch(auto) { app.dispatchFlow.open({ call_t: new Date(app.rows[app.peakIdx].t).toISOString() }); if (auto) setTimeout(() => app.dispatchFlow.run(), 2200); },
  say(text) { $('story-step').textContent = text; },
  storyStep(label, i, n) { $('story-step').textContent = label ? `story ${i}/${n} — ${label}` : ''; },
  setAlert(on) { document.body.classList.toggle('alerting', on); },
  statusNote(t) { $('m-status').textContent = t; },
  applyDispatch(res, event) {
    app.dispatchResult = res;
    app.dispatchEvent = event;
    const p = $('p-dispatch');
    p.style.display = 'block';
    clear(p);
    p.appendChild(DispatchFlow.resultNode(res, event));
    $('m-status').textContent = 'dispatched';
  },
};
Object.assign(app, { setAlert: publicApi.setAlert, applyDispatch: publicApi.applyDispatch, statusNote: publicApi.statusNote });
