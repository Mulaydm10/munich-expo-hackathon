/* Canvas 2D charts. Draw only — every number shown comes from the API layer.
 * Missing rows (null) break the line; they are never interpolated or zeroed. */

const C = {
  ink: '#edeff5', dim: '#a4abbb', faint: '#6e7585',
  grid: '#16203400', gridline: '#161c28',
  blue: '#5a8af5', cyan: '#46c9dd', lime: '#aacd4e', grey: '#8a8f9c',
  amber: '#e9a94b', red: '#e5675f', band: 'rgba(70,201,221,0.15)',
  panel: '#0c1019',
};

function dpr(canvas) {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return null; // no layout yet — caller skips this pass
  const r = Math.min(devicePixelRatio || 1, 2);
  if (canvas.width !== Math.round(w * r) || canvas.height !== Math.round(h * r)) {
    canvas.width = Math.round(w * r); canvas.height = Math.round(h * r);
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(r, 0, 0, r, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}
const berlinHour = (t) => Number(new Date(t).toLocaleString('en-GB', { timeZone: 'Europe/Berlin', hour: '2-digit', hour12: false }));
const berlinHM = (t) => new Date(t).toLocaleTimeString('en-GB', { timeZone: 'Europe/Berlin', hour: '2-digit', minute: '2-digit' });

/* ---------------- main load chart ---------------- */
export function drawMain(canvas, state) {
  const { rows = [], cursor = null, showEnvelope = false, showBand = true, showFirm = false, dispatch = null, xray = false } = state;
  const m = dpr(canvas);
  if (!m || !rows.length) return;
  const { ctx, w, h } = m;
  const padL = 78, padR = 26, padT = 26, padB = 40;
  const plotW = w - padL - padR, plotH = h - padT - padB;

  let maxV = 0;
  rows.forEach((r) => {
    // scale on the load curves (and the envelope when shown) — the forecast
    // band may clip rather than shrink the proof curves
    [r.load_kw_baseline, r.load_kw_optimised, showFirm ? r.firm_kw : null, showEnvelope ? r.envelope_kw : null]
      .forEach((v) => { if (v != null && v > maxV) maxV = v; });
  });
  if (dispatch) dispatch.rows.forEach((r) => { if (r.load_kw_committed != null) maxV = Math.max(maxV, r.load_kw_committed); });
  const top = Math.ceil((maxV * 1.1) / 200) * 200;
  const x = (i) => padL + (plotW * i) / (rows.length - 1);
  const y = (v) => padT + plotH - (plotH * v) / top;

  // grid + y axis
  ctx.font = '13px ui-sans-serif, system-ui, sans-serif';
  ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  for (let v = 0; v <= top; v += 200) {
    ctx.strokeStyle = C.gridline; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(padL, Math.round(y(v)) + 0.5); ctx.lineTo(w - padR, Math.round(y(v)) + 0.5); ctx.stroke();
    ctx.fillStyle = C.faint; ctx.fillText(v.toLocaleString('en-GB'), padL - 10, y(v));
  }
  ctx.save(); ctx.translate(20, padT + plotH / 2); ctx.rotate(-Math.PI / 2);
  ctx.textAlign = 'center'; ctx.fillStyle = C.dim; ctx.fillText('portfolio load (kW)', 0, 0); ctx.restore();

  // x axis — Berlin local hours
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  rows.forEach((r, i) => {
    const hh = berlinHM(r.t);
    if (!hh.endsWith(':00') || Number(hh.slice(0, 2)) % 3 !== 0) return;
    ctx.strokeStyle = C.gridline;
    ctx.beginPath(); ctx.moveTo(Math.round(x(i)) + 0.5, padT); ctx.lineTo(Math.round(x(i)) + 0.5, padT + plotH); ctx.stroke();
    ctx.fillStyle = C.faint; ctx.fillText(hh, x(i), padT + plotH + 9);
  });
  ctx.fillStyle = C.dim; ctx.textAlign = 'right';
  ctx.fillText('Europe/Berlin', w - padR, padT + plotH + 24);

  // compliance window
  if (dispatch) {
    const idx = dispatch.rows.map((r, i) => (r.in_compliance_window ? i : -1)).filter((i) => i >= 0);
    if (idx.length) {
      const a = x(idx[0]), b = x(idx[idx.length - 1]);
      ctx.fillStyle = 'rgba(233,169,75,0.10)';
      ctx.fillRect(a, padT, b - a, plotH);
      ctx.strokeStyle = 'rgba(233,169,75,0.55)'; ctx.setLineDash([5, 4]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(a, padT); ctx.lineTo(a, padT + plotH); ctx.moveTo(b, padT); ctx.lineTo(b, padT + plotH); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = C.amber; ctx.textAlign = 'left'; ctx.textBaseline = 'top';
      ctx.font = '600 13px ui-sans-serif, system-ui, sans-serif';
      ctx.fillText('compliance window', a + 7, padT + 6);
    }
  }

  // q05–q95 band (segments, broken at nulls)
  if (showBand) {
    ctx.fillStyle = C.band;
    let seg = [];
    const flush = () => {
      if (seg.length > 1) {
        ctx.beginPath();
        seg.forEach((p, k) => (k ? ctx.lineTo(x(p.i), y(p.hi)) : ctx.moveTo(x(p.i), y(p.hi))));
        for (let k = seg.length - 1; k >= 0; k--) ctx.lineTo(x(seg[k].i), y(seg[k].lo));
        ctx.closePath(); ctx.fill();
      }
      seg = [];
    };
    rows.forEach((r, i) => {
      if (r.forecast_kw_q05 == null || r.forecast_kw_q95 == null) flush();
      else seg.push({ i, lo: r.forecast_kw_q05, hi: r.forecast_kw_q95 });
    });
    flush();
  }

  const line = (key, color, width, dash = [], src = rows) => {
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash);
    ctx.lineJoin = 'round'; ctx.beginPath();
    let pen = false;
    src.forEach((r, i) => {
      const v = r[key];
      if (v == null) { pen = false; return; }
      if (!pen) { ctx.moveTo(x(i), y(v)); pen = true; } else ctx.lineTo(x(i), y(v));
    });
    ctx.stroke(); ctx.setLineDash([]);
  };

  if (showEnvelope) {
    line('envelope_kw', '#66c98c', 2, [9, 5]);
    const ev = rows[0].envelope_kw;
    ctx.fillStyle = '#66c98c'; ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
    ctx.font = '600 13px ui-sans-serif, system-ui, sans-serif';
    ctx.fillText(`transformer envelope ${ev.toLocaleString('en-GB')} kW`, padL + 8, y(ev) - 6);
  }
  if (showFirm) line('firm_kw', C.lime, 2, [3, 3]);

  if (dispatch) {
    line('load_kw_committed', C.grey, 2.5, [7, 5], dispatch.rows);
    line('load_kw_amended', C.blue, 4, [], dispatch.rows);
  } else {
    line('load_kw_baseline', C.grey, 2.5, [7, 5]);
    line('load_kw_optimised', C.blue, 4);
  }

  // missing-data markers
  rows.forEach((r, i) => {
    if (r.load_kw_baseline != null) return;
    ctx.fillStyle = 'rgba(233,169,75,0.32)';
    ctx.fillRect(x(i) - 3, padT, 6, plotH);
  });

  // peak markers
  const peak = (key, color, label, src = rows) => {
    let bi = -1, bv = -Infinity;
    src.forEach((r, i) => { if (r[key] != null && r[key] > bv) { bv = r[key]; bi = i; } });
    if (bi < 0) return;
    const px = x(bi), py = y(bv);
    ctx.strokeStyle = color; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(px, py, 5.5, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = '#090c14'; ctx.beginPath(); ctx.arc(px, py, 4, 0, Math.PI * 2); ctx.fill();
    const txt = `${label} ${Math.round(bv).toLocaleString('en-GB')} kW · ${berlinHM(src[bi].t)}`;
    ctx.font = '650 15px ui-sans-serif, system-ui, sans-serif';
    const tw = ctx.measureText(txt).width;
    const lx = Math.min(Math.max(px - tw / 2, padL), w - padR - tw - 8);
    const ly = py - 30;
    ctx.fillStyle = 'rgba(7,10,17,0.92)';
    ctx.fillRect(lx - 7, ly - 4, tw + 14, 24);
    ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.strokeRect(lx - 7, ly - 4, tw + 14, 24);
    ctx.fillStyle = color; ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    ctx.fillText(txt, lx, ly + 1);
  };
  if (dispatch) {
    peak('load_kw_committed', C.grey, 'committed peak', dispatch.rows);
    peak('load_kw_amended', C.blue, 'amended peak', dispatch.rows);
  } else {
    peak('load_kw_baseline', C.grey, 'baseline peak');
    peak('load_kw_optimised', C.blue, 'optimised peak');
  }

  // playback cursor
  if (cursor != null && rows[cursor]) {
    const cx = Math.round(x(cursor)) + 0.5;
    ctx.strokeStyle = 'rgba(237,239,245,0.5)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(cx, padT); ctx.lineTo(cx, padT + plotH); ctx.stroke();
    ctx.fillStyle = C.ink; ctx.font = '650 14px ui-sans-serif, system-ui, sans-serif';
    ctx.textAlign = cx > w - 150 ? 'right' : 'left'; ctx.textBaseline = 'top';
    ctx.fillText(berlinHM(rows[cursor].t), cx + (cx > w - 150 ? -8 : 8), padT + 2);
  }
  if (xray) {
    ctx.fillStyle = 'rgba(70,201,221,0.05)';
    ctx.fillRect(padL, padT, plotW, plotH);
  }
}

/* ---------------- price strip ---------------- */
export function drawPrice(canvas, rows, cursor = null) {
  const m = dpr(canvas);
  if (!m || !rows.length) return;
  const { ctx, w, h } = m;
  const padL = 78, padR = 26, padT = 12, padB = 16;
  const plotW = w - padL - padR, plotH = h - padT - padB;
  const vals = rows.map((r) => r.price_eur_mwh).filter((v) => v != null);
  const lo = Math.min(...vals) * 0.9, hi = Math.max(...vals) * 1.05;
  const x = (i) => padL + (plotW * i) / (rows.length - 1);
  const y = (v) => padT + plotH - (plotH * (v - lo)) / (hi - lo);
  ctx.beginPath();
  ctx.moveTo(x(0), padT + plotH);
  rows.forEach((r, i) => ctx.lineTo(x(i), y(r.price_eur_mwh)));
  ctx.lineTo(x(rows.length - 1), padT + plotH); ctx.closePath();
  ctx.fillStyle = 'rgba(233,169,75,0.13)'; ctx.fill();
  ctx.strokeStyle = C.amber; ctx.lineWidth = 2; ctx.beginPath();
  rows.forEach((r, i) => (i ? ctx.lineTo(x(i), y(r.price_eur_mwh)) : ctx.moveTo(x(i), y(r.price_eur_mwh))));
  ctx.stroke();
  ctx.font = '12px ui-sans-serif, system-ui, sans-serif'; ctx.fillStyle = C.faint;
  ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  ctx.fillText(`${Math.round(hi)}`, padL - 10, y(hi));
  ctx.fillText(`${Math.round(lo)}`, padL - 10, y(lo));
  ctx.textAlign = 'left'; ctx.fillStyle = C.amber;
  ctx.fillText('day-ahead price €/MWh', padL + 6, padT + 8);
  if (cursor != null && rows[cursor]) {
    const cx = Math.round(x(cursor)) + 0.5;
    ctx.strokeStyle = 'rgba(237,239,245,0.42)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(cx, padT); ctx.lineTo(cx, padT + plotH); ctx.stroke();
  }
}

/* ---------------- small illustrations (landing) ---------------- */
export function drawQuantileSketch(canvas, rows) {
  const m = dpr(canvas);
  if (!m || !rows.length) return;
  const { ctx, w, h } = m;
  const pad = 14, plotW = w - pad * 2, plotH = h - pad * 2;
  const top = Math.max(...rows.map((r) => r.forecast_kw_q95 || 0)) * 1.08;
  const x = (i) => pad + (plotW * i) / (rows.length - 1);
  const y = (v) => pad + plotH - (plotH * v) / top;
  ctx.fillStyle = C.band; ctx.beginPath();
  rows.forEach((r, i) => (r.forecast_kw_q95 != null) && (i ? ctx.lineTo(x(i), y(r.forecast_kw_q95)) : ctx.moveTo(x(i), y(r.forecast_kw_q95))));
  for (let i = rows.length - 1; i >= 0; i--) if (rows[i].forecast_kw_q05 != null) ctx.lineTo(x(i), y(rows[i].forecast_kw_q05));
  ctx.closePath(); ctx.fill();
  ctx.strokeStyle = C.cyan; ctx.lineWidth = 2; ctx.beginPath();
  let pen = false;
  rows.forEach((r, i) => { if (r.load_kw_baseline == null) { pen = false; return; } pen ? ctx.lineTo(x(i), y(r.load_kw_baseline)) : ctx.moveTo(x(i), y(r.load_kw_baseline)); pen = true; });
  ctx.stroke();
}

export function drawCompareSketch(canvas, rows) {
  const m = dpr(canvas);
  if (!m || !rows.length) return;
  const { ctx, w, h } = m;
  const pad = 14, plotW = w - pad * 2, plotH = h - pad * 2;
  const top = Math.max(...rows.map((r) => r.load_kw_baseline || 0)) * 1.12;
  const x = (i) => pad + (plotW * i) / (rows.length - 1);
  const y = (v) => pad + plotH - (plotH * v) / top;
  const line = (key, color, width, dash) => {
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash || []);
    ctx.beginPath(); let pen = false;
    rows.forEach((r, i) => { const v = r[key]; if (v == null) { pen = false; return; } pen ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v)); pen = true; });
    ctx.stroke(); ctx.setLineDash([]);
  };
  line('load_kw_baseline', C.grey, 2, [6, 4]);
  line('load_kw_optimised', C.blue, 3);
}

export function drawPooling(canvas, points) {
  const m = dpr(canvas);
  if (!m || !points || !points.length) return;
  const { ctx, w, h } = m;
  const pad = 26, plotW = w - pad * 2, plotH = h - pad * 2;
  const maxN = Math.max(...points.map((p) => p.n_sites));
  const maxV = Math.max(...points.map((p) => p.firm_kw_per_site)) * 1.1;
  const x = (n) => pad + (plotW * Math.log10(n)) / Math.log10(maxN);
  const y = (v) => pad + plotH - (plotH * v) / maxV;
  ctx.strokeStyle = C.lime; ctx.lineWidth = 3; ctx.beginPath();
  points.forEach((p, i) => (i ? ctx.lineTo(x(p.n_sites), y(p.firm_kw_per_site)) : ctx.moveTo(x(p.n_sites), y(p.firm_kw_per_site))));
  ctx.stroke();
  ctx.fillStyle = C.faint; ctx.font = '12px ui-sans-serif, system-ui, sans-serif';
  ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
  ctx.fillText('firm kW per site', pad, pad - 6);
  ctx.textAlign = 'right'; ctx.textBaseline = 'top';
  ctx.fillText('sites pooled (log)', w - pad, h - pad + 6);
}

/* heartbeat waveform for the command bar */
export function drawHeartbeat(canvas, rows, cursor, calm) {
  const m = dpr(canvas);
  if (!m || !rows.length) return;
  const { ctx, w, h } = m;
  const row = rows[Math.min(cursor, rows.length - 1)];
  const amp = row && row.load_kw_optimised != null ? row.load_kw_optimised / 1600 : 0.2;
  const t = performance.now() / 1000;
  ctx.strokeStyle = calm ? 'rgba(90,138,245,0.45)' : 'rgba(233,169,75,0.45)';
  ctx.lineWidth = 1.5; ctx.beginPath();
  for (let px = 0; px <= w; px += 4) {
    const k = px / w;
    const v = Math.sin(k * 26 + t * (calm ? 1.1 : 2.0)) * Math.exp(-Math.pow((k - 0.5) * 2.2, 2));
    const yy = h / 2 + v * h * 0.2 * (0.3 + amp);
    px ? ctx.lineTo(px, yy) : ctx.moveTo(px, yy);
  }
  ctx.stroke();
}
