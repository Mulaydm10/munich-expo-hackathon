// Screen 2 — one day. Baseline vs optimised load, the thermal envelope as a band, the
// sold reduction floor shaded, price on the right axis. Space (or the button) plays
// the day back.
//
// Every series is plotted straight off /api/scenario/{id}/timeseries. Missing points
// break the line rather than being interpolated: a gap must look like a gap.

import { readPayload, drawUnavailable, scaler, extent, berlinClock, runs } from "./payload.js";

const canvas = document.getElementById("day-canvas");
const rows = readPayload("intervals");
const clock = document.getElementById("play-clock");
const playBtn = document.getElementById("play");

const PAD = { l: 90, r: 100, t: 20, b: 60 };

if (canvas && !Array.isArray(rows)) {
  drawUnavailable(canvas, "Timeseries payload unreadable — not a flat line at zero");
} else if (canvas && rows.length) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width;
  const H = canvas.height;
  let cursor = rows.length - 1;
  let timer = null;

  const kwKeys = ["baseline_load_kw", "optimised_load_kw", "envelope_kw", "firm_kw"];
  const kwExtents = kwKeys.map((k) => extent(rows, k)).filter(Boolean);
  const kwLo = kwExtents.length ? Math.min(0, ...kwExtents.map((e) => e[0])) : 0;
  const kwHi = kwExtents.length ? Math.max(...kwExtents.map((e) => e[1])) : 1;
  const priceExt = extent(rows, "price_eur_mwh");

  const x = scaler(0, Math.max(1, rows.length - 1), PAD.l, W - PAD.r);
  const yKw = scaler(kwLo, kwHi, H - PAD.b, PAD.t);
  const yPrice = priceExt ? scaler(priceExt[0], priceExt[1], H - PAD.b, PAD.t) : null;

  function line(key, { dash = [], width = 3, colour = "#000" } = {}) {
    const yFor = key === "price_eur_mwh" ? yPrice : yKw;
    ctx.save();
    ctx.setLineDash(dash);
    ctx.lineWidth = width;
    ctx.strokeStyle = colour;
    for (const run of runs(rows, key)) {
      if (run.length === 1) {
        // An isolated point has no segment to stroke. A dot keeps a datum the API did
        // send from vanishing into what a viewer would read as a gap.
        ctx.beginPath();
        ctx.arc(x(run[0].i), yFor(run[0].v), Math.max(2, width), 0, Math.PI * 2);
        ctx.fillStyle = colour;
        ctx.fill();
        continue;
      }
      ctx.beginPath();
      run.forEach((pt, n) => {
        const px = x(pt.i);
        const py = yFor(pt.v);
        if (n === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      });
      ctx.stroke();
    }
    ctx.restore();
  }

  function band(key, colour) {
    ctx.save();
    ctx.fillStyle = colour;
    ctx.globalAlpha = 0.18;
    // ONE POLYGON PER CONTIGUOUS RUN. The single-polygon version skipped missing rows
    // and joined the survivors, so a gap in envelope_kw or firm_kw came out as shaded
    // area spanning it — capacity drawn where the API sent no data, which is the same
    // lie as a flat line at zero, only harder to see. The line renderer already broke
    // at gaps; the band did not, so the two disagreed on the same chart.
    for (const run of runs(rows, key)) {
      if (run.length < 2) continue;   // a single point has no area; do not invent one
      ctx.beginPath();
      ctx.moveTo(x(run[0].i), yKw(0));
      for (const pt of run) ctx.lineTo(x(pt.i), yKw(pt.v));
      ctx.lineTo(x(run[run.length - 1].i), yKw(0));
      ctx.closePath();
      ctx.fill();
    }
    ctx.restore();
  }

  function axes() {
    ctx.save();
    ctx.strokeStyle = "#10161c";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(PAD.l, PAD.t);
    ctx.lineTo(PAD.l, H - PAD.b);
    ctx.lineTo(W - PAD.r, H - PAD.b);
    ctx.stroke();
    ctx.fillStyle = "#414d58";
    ctx.font = "16px system-ui, sans-serif";
    ctx.textAlign = "right";
    for (let i = 0; i <= 4; i += 1) {
      const v = kwLo + ((kwHi - kwLo) * i) / 4;
      ctx.fillText(`${Math.round(v).toLocaleString()}`, PAD.l - 10, yKw(v) + 5);
    }
    ctx.save();
    ctx.translate(22, H / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textAlign = "center";
    ctx.fillText("load / capacity (kW)", 0, 0);
    ctx.restore();
    if (priceExt) {
      ctx.textAlign = "left";
      for (let i = 0; i <= 4; i += 1) {
        const v = priceExt[0] + ((priceExt[1] - priceExt[0]) * i) / 4;
        ctx.fillText(`${Math.round(v).toLocaleString()}`, W - PAD.r + 10, yPrice(v) + 5);
      }
      ctx.save();
      ctx.translate(W - 18, H / 2);
      ctx.rotate(Math.PI / 2);
      ctx.textAlign = "center";
      ctx.fillText("price (EUR/MWh)", 0, 0);
      ctx.restore();
    }
    ctx.textAlign = "center";
    const step = Math.max(1, Math.floor(rows.length / 8));
    for (let i = 0; i < rows.length; i += step) {
      ctx.fillText(berlinClock(rows[i].t), x(i), H - PAD.b + 26);
    }
    ctx.fillText("time of day (Europe/Berlin)", W / 2, H - 12);
    ctx.restore();
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    axes();
    band("envelope_kw", "#2f7d4f");
    band("firm_kw", "#7a3fa8");
    line("envelope_kw", { colour: "#2f7d4f", width: 2, dash: [2, 4] });
    line("firm_kw", { colour: "#7a3fa8", width: 2, dash: [8, 4] });
    line("baseline_load_kw", { colour: "#6a6f76", width: 3, dash: [10, 6] });
    line("optimised_load_kw", { colour: "#0b6ea8", width: 4 });
    if (priceExt) line("price_eur_mwh", { colour: "#a8500b", width: 2, dash: [4, 3] });
    // Playback cursor.
    ctx.save();
    ctx.strokeStyle = "#b3261e";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(x(cursor), PAD.t);
    ctx.lineTo(x(cursor), H - PAD.b);
    ctx.stroke();
    ctx.restore();
    if (clock) clock.textContent = berlinClock(rows[cursor].t);
  }

  function stop() {
    clearInterval(timer);
    timer = null;
    if (playBtn) playBtn.textContent = "▶ Play the day";
  }

  function play() {
    if (timer) { stop(); return; }
    cursor = 0;
    if (playBtn) playBtn.textContent = "⏸ Pause";
    timer = setInterval(() => {
      cursor += 1;
      if (cursor >= rows.length) { cursor = rows.length - 1; stop(); }
      draw();
    }, 60);
  }

  playBtn?.addEventListener("click", play);
  draw();
}
