// Screen 3 — the call. Press d (or the button), POST the reduction event, and the
// server re-renders this screen with the dispatch response.
//
// The POST goes to the URL the server put on the button; this module does not build
// route strings of its own. Nothing about the result is computed here — promised,
// delivered and the shortfall are read straight out of the response by the template.

import { readPayload, drawUnavailable, scaler, extent, berlinClock, runs } from "./payload.js";

const btn = document.getElementById("dispatch");

// Report a failure WITHOUT building markup from it.
//
// `error`, `detail` and `how_to_fix` are strings the API controls, and the version of
// this file that interpolated them into an HTML-parsing sink turned a crafted error body
// into live markup in the service's own origin. The Jinja half of this page is
// autoescaped; this client-side path bypassed that escaping entirely. So: the element
// is constructed here, and every API-controlled string arrives through textContent,
// which can only ever become text. The caught exception took the same path and needed
// the same fix: interpolating a thrown object is still interpolating a string this code
// did not author, and it went into the same sink as the response fields.
function showFailure(testid, label, message) {
  const p = document.createElement("p");
  p.className = "value-missing";
  p.dataset.testid = testid;
  p.textContent = `${label}: ${message}`;
  const prior = document.querySelector(`[data-testid="${testid}"]`);
  if (prior) prior.remove();          // one message per failure, not a growing stack
  btn.insertAdjacentElement("afterend", p);
}

btn?.addEventListener("click", async () => {
  const url = btn.dataset.dispatchUrl;
  if (!url) return;
  // The ReductionEvent the server rendered onto this page: {call_t, notice_min,
  // duration_min, reduction_kw}. It is POSTed verbatim. This module does not build one
  // — a reduction size or a notice period chosen in JavaScript would be this page
  // computing a figure, which contracts/src/ui.md forbids outright — and it does not
  // fall back to an empty body, which is what the previous version sent and what a
  // validating service must reject, leaving the demo's headline action dead.
  const event = readPayload("reduction-event");
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    showFailure(
      "dispatch-no-event",
      "Cannot dispatch",
      "the API did not provide a reduction event, and this page will not invent one.",
    );
    return;
  }
  btn.disabled = true;
  btn.textContent = "Dispatching…";
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(event),
    });
    if (!res.ok) {
      // Show the API's own {error, detail, how_to_fix}. Never a silent no-op.
      let detail = `${res.status} ${res.statusText}`;
      try {
        const body = await res.json();
        detail = [body.error, body.detail, body.how_to_fix].filter(Boolean).join(" — ") || detail;
      } catch (_) { /* body was not JSON; the status line is what we have */ }
      showFailure("dispatch-failed", "Dispatch failed", detail);
      btn.textContent = "Dispatch failed";
      return;
    }
    window.location.reload();
  } catch (err) {
    showFailure(
      "dispatch-unreachable",
      "Dispatch could not reach the API",
      err && err.message ? err.message : String(err),
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
    for (const run of runs(rows, key)) {
      if (run.length === 1) {
        ctx.beginPath();
        ctx.arc(x(run[0].i), y(run[0].v), Math.max(2, width), 0, Math.PI * 2);
        ctx.fillStyle = colour;
        ctx.fill();
        continue;
      }
      ctx.beginPath();
      run.forEach((pt, n) => {
        if (n === 0) ctx.moveTo(x(pt.i), y(pt.v)); else ctx.lineTo(x(pt.i), y(pt.v));
      });
      ctx.stroke();
    }
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
