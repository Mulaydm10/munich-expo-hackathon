// Screen 4 — pooling. The safe promise per site against portfolio size, with the
// shortfall rate on the right axis.
//
// The curve is /api/scenario/{id}/pooling, i.e. src/market's diversification_curve
// rows, plotted point for point. Nothing is fitted, smoothed or extrapolated here:
// the project's central claim has to survive on the points the model produced, and a
// browser-side trend line would be a claim this lane is not entitled to make.

import { readPayload, drawUnavailable, scaler, extent } from "./payload.js";

const canvas = document.getElementById("pooling-canvas");
const slider = document.getElementById("n-sites-slider");
const readout = document.getElementById("n-sites-readout");
const rows = readPayload("curve");

if (canvas && !Array.isArray(rows)) {
  drawUnavailable(canvas, "Pooling payload unreadable — not a flat curve at zero");
} else if (canvas && rows.length) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width;
  const H = canvas.height;
  const PAD = { l: 100, r: 110, t: 30, b: 70 };
  let upTo = rows.length - 1;

  const firmExt = extent(rows, "firm_kw_per_site");
  const rateExt = extent(rows, "shortfall_rate");
  const x = scaler(0, Math.max(1, rows.length - 1), PAD.l, W - PAD.r);
  const yFirm = firmExt ? scaler(0, firmExt[1], H - PAD.b, PAD.t) : null;
  // Rate axis is pinned to [0, max(observed, 0.05)] so a flat line near zero reads as
  // flat-and-low rather than being stretched to fill the panel.
  const yRate = rateExt ? scaler(0, Math.max(rateExt[1], 0.05), H - PAD.b, PAD.t) : null;

  function axes() {
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
    if (yFirm) {
      for (let i = 0; i <= 4; i += 1) {
        const v = (firmExt[1] * i) / 4;
        ctx.fillText(v.toFixed(1), PAD.l - 10, yFirm(v) + 5);
      }
      ctx.save();
      ctx.translate(24, H / 2);
      ctx.rotate(-Math.PI / 2);
      ctx.textAlign = "center";
      ctx.fillText("safe promise per site (kW)", 0, 0);
      ctx.restore();
    }
    if (yRate) {
      ctx.textAlign = "left";
      const top = Math.max(rateExt[1], 0.05);
      for (let i = 0; i <= 4; i += 1) {
        const v = (top * i) / 4;
        ctx.fillText(`${(v * 100).toFixed(1)} %`, W - PAD.r + 10, yRate(v) + 5);
      }
      ctx.save();
      ctx.translate(W - 22, H / 2);
      ctx.rotate(Math.PI / 2);
      ctx.textAlign = "center";
      ctx.fillText("shortfall rate (%)", 0, 0);
      ctx.restore();
    }
    ctx.textAlign = "center";
    rows.forEach((r, i) => {
      if (i % Math.max(1, Math.floor(rows.length / 8)) === 0) {
        ctx.fillText(String(r.n_sites ?? "?"), x(i), H - PAD.b + 26);
      }
    });
    ctx.fillText("sites in the portfolio", W / 2, H - 16);
  }

  function series(key, yScale, colour, dash, marker) {
    ctx.save();
    ctx.setLineDash(dash);
    ctx.lineWidth = 4;
    ctx.strokeStyle = colour;
    ctx.beginPath();
    let open = false;
    rows.slice(0, upTo + 1).forEach((r, i) => {
      const v = r[key];
      if (typeof v !== "number" || !Number.isFinite(v)) { open = false; return; }
      if (!open) { ctx.moveTo(x(i), yScale(v)); open = true; } else { ctx.lineTo(x(i), yScale(v)); }
    });
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = colour;
    rows.slice(0, upTo + 1).forEach((r, i) => {
      const v = r[key];
      if (typeof v !== "number" || !Number.isFinite(v)) return;
      // Distinct marker shape per series, so the two lines are told apart without
      // colour (contracts/src/ui.md: never rely on colour alone).
      if (marker === "circle") {
        ctx.beginPath();
        ctx.arc(x(i), yScale(v), 5, 0, Math.PI * 2);
        ctx.fill();
      } else {
        ctx.fillRect(x(i) - 4, yScale(v) - 4, 8, 8);
      }
    });
    ctx.restore();
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    axes();
    if (yFirm) series("firm_kw_per_site", yFirm, "#0b6ea8", [], "circle");
    if (yRate) series("shortfall_rate", yRate, "#a8500b", [10, 6], "square");
    const r = rows[upTo] || {};
    if (readout) {
      const firm = typeof r.firm_kw_per_site === "number" ? `${r.firm_kw_per_site.toFixed(2)} kW/site` : "not provided by API";
      const rate = typeof r.shortfall_rate === "number" ? `${(r.shortfall_rate * 100).toFixed(2)} % shortfall` : "not provided by API";
      readout.textContent = `${r.n_sites ?? "?"} sites → ${firm}, ${rate}`;
    }
  }

  slider?.addEventListener("input", () => {
    upTo = Number(slider.value);
    draw();
  });

  draw();
}
