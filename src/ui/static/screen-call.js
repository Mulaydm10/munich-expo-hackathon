// Screen 3 — the call. Press d (or the button), POST the reduction event, and the
// server re-renders this screen with the dispatch response.
//
// The POST goes to the URL the server put on the button; this module does not build
// route strings of its own. Nothing about the result is computed here — promised,
// delivered and the shortfall are read straight out of the response by the template.

import { readPayload, drawUnavailable, scaler, extent, berlinClock } from "./payload.js";

const btn = document.getElementById("dispatch");

btn?.addEventListener("click", async () => {
  const url = btn.dataset.dispatchUrl;
  if (!url) return;
  btn.disabled = true;
  btn.textContent = "Dispatching…";
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}",
    });
    if (!res.ok) {
      // Show the API's own {error, detail, how_to_fix}. Never a silent no-op.
      let detail = `${res.status} ${res.statusText}`;
      try {
        const body = await res.json();
        detail = [body.error, body.detail, body.how_to_fix].filter(Boolean).join(" — ") || detail;
      } catch (_) { /* body was not JSON; the status line is what we have */ }
      btn.insertAdjacentHTML(
        "afterend",
        `<p class="value-missing" data-testid="dispatch-failed">Dispatch failed: ${detail}</p>`,
      );
      btn.textContent = "Dispatch failed";
      return;
    }
    window.location.reload();
  } catch (err) {
    btn.insertAdjacentHTML(
      "afterend",
      `<p class="value-missing">Dispatch could not reach the API: ${err}</p>`,
    );
    btn.textContent = "Dispatch failed";
  } finally {
    btn.disabled = false;
  }
});

const canvas = document.getElementById("call-canvas");
const rows = readPayload("amended");
if (canvas && rows && !Array.isArray(rows)) {
  drawUnavailable(canvas, "Amended timeseries unreadable — not a flat line at zero");
} else if (canvas && Array.isArray(rows) && rows.length) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width;
  const H = canvas.height;
  const PAD = { l: 90, r: 30, t: 20, b: 60 };
  const ext = ["load_kw_before", "load_kw_after"].map((k) => extent(rows, k)).filter(Boolean);
  const lo = ext.length ? Math.min(0, ...ext.map((e) => e[0])) : 0;
  const hi = ext.length ? Math.max(...ext.map((e) => e[1])) : 1;
  const x = scaler(0, Math.max(1, rows.length - 1), PAD.l, W - PAD.r);
  const y = scaler(lo, hi, H - PAD.b, PAD.t);

  // The reduction window, shaded, straight off each row's own flag.
  ctx.save();
  ctx.fillStyle = "#7a3fa8";
  ctx.globalAlpha = 0.15;
  rows.forEach((r, i) => {
    if (r.in_event) ctx.fillRect(x(i), PAD.t, Math.max(1, x(1) - x(0)), H - PAD.b - PAD.t);
  });
  ctx.restore();

  const line = (key, dash, colour, width) => {
    ctx.save();
    ctx.setLineDash(dash);
    ctx.lineWidth = width;
    ctx.strokeStyle = colour;
    ctx.beginPath();
    let open = false;
    rows.forEach((r, i) => {
      const v = r[key];
      if (typeof v !== "number" || !Number.isFinite(v)) { open = false; return; }
      if (!open) { ctx.moveTo(x(i), y(v)); open = true; } else { ctx.lineTo(x(i), y(v)); }
    });
    ctx.stroke();
    ctx.restore();
  };

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
    const v = lo + ((hi - lo) * i) / 4;
    ctx.fillText(`${Math.round(v).toLocaleString()} kW`, PAD.l - 10, y(v) + 5);
  }
  ctx.textAlign = "center";
  const step = Math.max(1, Math.floor(rows.length / 8));
  for (let i = 0; i < rows.length; i += step) ctx.fillText(berlinClock(rows[i].t), x(i), H - PAD.b + 26);
  ctx.fillText("time of day (Europe/Berlin)", W / 2, H - 12);
  ctx.restore();

  line("load_kw_before", [10, 6], "#6a6f76", 3);
  line("load_kw_after", [], "#0b6ea8", 4);
}
