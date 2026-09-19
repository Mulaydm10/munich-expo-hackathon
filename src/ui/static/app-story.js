/* Landing cinematics + judge autopilot.
 * Loaded directly by index.html (initLanding runs when #scene exists);
 * simulator.js imports runAutopilot from here. */
import * as api from './app-api.js';
import { drawQuantileSketch, drawCompareSketch, drawPooling } from './app-chart.js';
import { el, clear } from './app-dom.js';

const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------------- landing ---------------- */
async function initLanding() {
  const mount = document.getElementById('scene');
  const overlay = document.getElementById('overlay');
  const stateTag = document.getElementById('scene-state');
  const scn = await api.getScenario();
  const [ts, pool] = await Promise.all([api.getTimeseries(scn.id), api.getPooling(scn.id)]);
  const rows = ts.rows;

  // section figures — straight from the API layer, never recomputed here
  const set = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  const fa = scn.forecast_accuracy, tt = scn.totals;
  set('f-acc', api.fmt(fa.accuracy_pct, '%'));
  set('f-wape', api.fmt(fa.wape, '', 3));
  set('f-cov', api.fmt(fa.coverage_q05_q95, '', 2));
  set('s-base', api.fmt(tt.peak_kw_baseline, 'kW'));
  set('s-opt', api.fmt(tt.peak_kw_optimised, 'kW'));
  set('s-inf', api.fmt(scn.scorecard.infeasible_sites));
  set('g-firm', api.fmt(tt.pool_firm_mw, 'MW', 2));
  const rev = document.getElementById('g-rev');
  if (rev) {
    if (tt.capacity_revenue_eur === null) { rev.textContent = 'not available (no balancing-market source)'; rev.className = 'v missing'; }
    else rev.textContent = api.fmt(tt.capacity_revenue_eur, '€');
  }

  const sketches = () => {
    drawQuantileSketch(document.getElementById('sk-forecast'), rows);
    drawCompareSketch(document.getElementById('sk-compare'), rows);
    drawPooling(document.getElementById('sk-pool'), pool.points);
  };
  const sketchIds = ['sk-forecast', 'sk-compare', 'sk-pool'];
  const painted = () => sketchIds.every((id) => {
    const el = document.getElementById(id);
    const r = Math.min(devicePixelRatio || 1, 2);
    return el && el.clientWidth && el.width === Math.round(el.clientWidth * r);
  });
  sketches();
  // guaranteed first paint: poll until each canvas has layout, then stop
  const poll = setInterval(() => { sketches(); if (painted()) clearInterval(poll); }, 120);
  setTimeout(() => clearInterval(poll), 8000);
  addEventListener('resize', sketches);
  const ro = new ResizeObserver(sketches);
  sketchIds.forEach((id) => { const el = document.getElementById(id); if (el) ro.observe(el); });

  // ---- 3D scene, progressive and never blocking the buttons ----
  let scene = null;
  try {
    const mod = await import('./app-scene.js');
    if (!mod.webglAvailable()) throw new Error('no webgl');
    scene = new mod.DepotScene(mount, { mode: 'hero' });
    window.flexgridScene = scene; // demo console handle
    stateTag.replaceChildren(el('span', { className: 'dot' }), document.createTextNode(' live scene'));
    stateTag.className = 'tag ok';
  } catch (e) {
    document.getElementById('scene-fallback').style.display = 'block';
    stateTag.replaceChildren(el('span', { className: 'dot' }), document.createTextNode(' static view (no WebGL)'));
    stateTag.className = 'tag warn';
    console.warn('scene unavailable:', e.message);
    return;
  }

  // ---- scrubber: time, lighting, load and vehicle state move together ----
  const timeEl = document.getElementById('time');
  const clockEl = document.getElementById('clock');
  const rowForHour = (h) => {
    const hh = ((h % 24) + 24) % 24;
    const idx = Math.round((hh * 60) / 15) % rows.length;
    return { idx, row: rows[idx] };
  };
  const applyTime = (h) => {
    const { row } = rowForHour(h);
    scene.setTime(h);
    const v = row.load_kw_optimised ?? row.load_kw_baseline;
    scene.setLoad(v == null ? 0.12 : v / row.envelope_kw, { phase: 'optimised' });
    scene.setReady(h >= 26 ? 4 : h >= 23 ? 2 : 0);
    clockEl.textContent = `${String(Math.floor(((h % 24) + 24) % 24)).padStart(2, '0')}:${String(Math.round((h % 1) * 60)).padStart(2, '0')}`;
  };
  timeEl.addEventListener('input', () => { stopIntro(); applyTime(+timeEl.value); });

  // ---- pointer parallax + hover cards ----
  const hover = document.getElementById('hover');
  const THREE = await import('three');
  const ray = new THREE.Raycaster();
  const ptr = new THREE.Vector2();
  mount.addEventListener('pointermove', (e) => {
    const r = mount.getBoundingClientRect();
    const nx = ((e.clientX - r.left) / r.width) * 2 - 1;
    const ny = ((e.clientY - r.top) / r.height) * 2 - 1;
    scene.setParallax(nx, ny);
    ptr.set(nx, -ny);
    ray.setFromCamera(ptr, scene.camera);
    const hit = ray.intersectObjects(scene.model.children, true)[0];
    const card = hit && describe(hit.object, scn, rowForHour(+timeEl.value).row);
    if (card) {
      hover.replaceChildren(card);
      hover.style.display = 'block';
      hover.style.left = `${Math.min(e.clientX + 16, innerWidth - 260)}px`;
      hover.style.top = `${Math.min(e.clientY + 14, innerHeight - 150)}px`;
    } else hover.style.display = 'none';
  });
  mount.addEventListener('pointerleave', () => { hover.style.display = 'none'; scene.setParallax(0, 0); });

  // ---- opening sequence ----
  let cancelled = false;
  function stopIntro() { cancelled = true; overlay.classList.remove('on'); }
  document.getElementById('skip-intro').addEventListener('click', () => { stopIntro(); scene.flyTo(scene.home, undefined, 900); applyTime(+timeEl.value); });
  document.getElementById('story60').addEventListener('click', () => { cancelled = false; runIntro(); });

  async function beat(text, ms) {
    if (cancelled) return false;
    if (text !== null) {
      overlay.textContent = text;
      overlay.classList.add('on');
    }
    await sleep(ms);
    return !cancelled;
  }

  async function runIntro() {
    if (reduced) { applyTime(20); scene.flyTo(scene.home, undefined, 10); return; }
    const V = (x, y, z) => new THREE.Vector3(x, y, z);
    scene.camera.position.set(96, 46, 118);
    scene.setTime(18.5); scene.setLoad(0.1); scene.setReady(0); scene.setPeakWall(0);
    scene.flyTo(V(44, 17, 56), V(0, 3, 0), 3000);
    if (!await beat('Tomorrow\u2019s charging peak is already forming.', 2600)) return;
    for (let i = 1; i <= 16 && !cancelled; i++) { scene.setLoad(i / 22); await sleep(105); }
    if (!await beat('FlexGrid predicts it 36 hours ahead.', 1800)) return;
    scene.setTime(20.5);
    scene.flyTo(V(30, 11, 40), V(0, 3, 0), 2400);
    scene.setLoad(0.9, { phase: 'baseline' });
    scene.setPeakWall(1);
    if (!await beat(null, 1500)) return;
    if (!await beat('Charging moves. Departures do not.', 1900)) return;
    scene.shiftPulses(0.55);
    scene.setLoad(0.72, { phase: 'optimised' });
    for (let k = 10; k >= 0 && !cancelled; k--) { scene.setPeakWall(k / 10 * 0.55); await sleep(90); }
    scene.setReady(4);
    if (!await beat('Peak reduced. Fleet ready.', 2100)) return;
    overlay.classList.remove('on');
    scene.flyTo(scene.home, V(0, 3, 0), 2200);
    scene.setTime(+timeEl.value);
    // hand the load pulse to the simulator chart (real cross-page transition)
    sessionStorage.setItem('flexgrid_handoff', JSON.stringify({
      from: 'landing', at: Date.now(),
      peak_kw_baseline: scn.totals.peak_kw_baseline,
      peak_kw_optimised: scn.totals.peak_kw_optimised,
    }));
  }

  applyTime(20);
  runIntro();
}

function describe(obj, scn, row) {
  let g = obj, name = '';
  while (g && !/^(charger_\d+|car_\d+|transformer|canopy|column_\d+)$/.test(g.name)) g = g.parent;
  name = g ? g.name : '';
  if (/^charger_(\d+)$/.test(name)) {
    const n = name.split('_')[1];
    return el('div', {},
      el('h4', { text: `Charger ${n}` }),
      el('div', { className: 'kv' }, el('span', { className: 'k', text: 'Status' }), el('span', { className: 'v', text: 'scheduled' })),
      el('div', { className: 'kv' }, el('span', { className: 'k', text: 'Rated power' }), el('span', { className: 'v', text: api.MISSING })),
      el('div', { className: 'kv' }, el('span', { className: 'k', text: 'Site' }), el('span', { className: 'v mono', text: api.MISSING })),
      el('p', { className: 'faint', text: 'Session data is synthesized.' }));
  }
  if (/^car_(\d+)$/.test(name)) {
    const n = name.split('_')[1];
    return el('div', {},
      el('h4', { text: `Vehicle ${n}` }),
      el('div', { className: 'kv' }, el('span', { className: 'k', text: 'Energy required' }), el('span', { className: 'v', text: api.MISSING })),
      el('div', { className: 'kv' }, el('span', { className: 'k', text: 'Departure' }), el('span', { className: 'v', text: api.MISSING })),
      el('div', { className: 'kv' }, el('span', { className: 'k', text: 'Deadline misses' }), el('span', { className: 'v', text: api.fmt(scn.scorecard.deadline_misses) })));
  }
  if (name === 'transformer') {
    const load = row.load_kw_optimised ?? row.load_kw_baseline;
    return el('div', {},
      el('h4', { text: 'Transformer' }),
      el('div', { className: 'kv' }, el('span', { className: 'k', text: 'Load now' }), el('span', { className: 'v', text: api.fmt(load, 'kW') })),
      el('div', { className: 'kv' }, el('span', { className: 'k', text: 'Envelope' }), el('span', { className: 'v', text: api.fmt(row.envelope_kw, 'kW') })),
      el('p', { className: 'faint', text: 'Envelope is an assumed rating.' }));
  }
  return null;
}

/* ---------------- judge autopilot (simulator) ---------------- */
export function runAutopilot(app) {
  const steps = [
    { label: 'Germany → Munich', ms: 16000, run: () => { app.setMapZoom('de'); setTimeout(() => app.setMapZoom('muc'), 4000); app.say(`Loaded ${app.scn.spec.n_sites ?? api.MISSING} registered charging locations for ${app.scn.spec.date}.`); } },
    { label: 'Depot load forming', ms: 30000, run: () => { app.play(true); app.say('Baseline charging demand builds toward the evening peak.'); } },
    { label: 'Baseline vs optimised peak', ms: 40000, run: () => { app.goToPeak(); app.say(`Baseline peak ${app.scn.totals.peak_kw_baseline} kW, optimised ${app.scn.totals.peak_kw_optimised} kW.`); } },
    { label: 'Forecast quality', ms: 34000, run: () => { app.highlight('forecast'); app.say(`Forecast accuracy ${api.fmt(app.scn.forecast_accuracy.accuracy_pct, '%')} on portfolio load over ${api.fmt(app.scn.forecast_accuracy.history_days, 'days')}.`); } },
    { label: 'Dispatch request', ms: 46000, run: () => { app.openDispatch(true); app.say('Grid operator dispatch request is sent to the backend.'); } },
    { label: 'Pooling', ms: 34000, run: () => { app.highlight('pooling'); app.say('Firm flexibility per site rises as sites are pooled.'); } },
    { label: 'Evidence & limitations', ms: 40000, run: () => { app.highlight('evidence'); app.say('Real public data for load, weather, price and locations. Sessions are synthesized.'); } },
  ];
  let i = 0, timer = null, paused = false;
  const next = () => {
    if (paused || i >= steps.length) return;
    const s = steps[i++];
    app.storyStep(s.label, i, steps.length);
    s.run();
    timer = setTimeout(next, s.ms);
  };
  next();
  return {
    pause() { paused = true; clearTimeout(timer); app.play(false); },
    resume() { paused = false; next(); },
    stop() { paused = true; clearTimeout(timer); app.storyStep(null); },
  };
}

if (document.getElementById('scene')) initLanding();
