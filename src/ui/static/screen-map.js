// Screen 1 — the map. Every site from /api/sites on one <canvas>.
//
// Canvas, not DOM: contracts/src/ui.md requires the map to stay interactive at
// national scale, and tens of thousands of <div>s do not. At low zoom the points are
// binned into screen cells and the cell label is the NUMBER OF SITES in it — a count
// of rows, never a summed capacity, which this page is not allowed to compute.
//
// No tile layer and no mapping library on purpose: a basemap means calls to a tile
// server, and "loads with the API reachable and nothing else" is a hard guarantee.
// Coastline-free is fine; the dots carry the geography.

import { readPayload, drawUnavailable, toCanvasPoint } from "./payload.js";

// Display buckets, mirroring MAP_LEGEND_BINS in src/ui/api.py (the legend table is
// rendered server-side from that constant; keep the two in step). Each bucket has a
// distinct radius AND a distinct fill, so the map never relies on colour alone.
const BINS = [
  { max: 50, r: 2.0, fill: "#9fb3c8", ring: false },
  { max: 200, r: 3.5, fill: "#4a7fa5", ring: false },
  { max: 500, r: 5.0, fill: "#0b6ea8", ring: false },
  { max: Infinity, r: 7.0, fill: "#ffffff", ring: true },
];

// Not a bucket. A site whose firm_kw the API did not send has no capacity to bucket, so
// it gets a mark of its own: hollow, crossed, and never filled. Mirrors MAP_UNKNOWN_MARK
// in src/ui/api.py, which is the legend row that names it.
const UNKNOWN_MARK = { r: 5.0, stroke: "#b3261e", width: 2.5 };
const UNKNOWN_TEXT = "capacity not provided by API";

// The site's own firm_kw, or null. NOT 0. Coercing a missing capacity to zero put an
// unknown site in the smallest-capacity bucket — a filled low dot — so "this site can
// promise almost nothing" and "we do not know what this site can promise" were the same
// mark on the demo's first screen. They are opposite facts to an operator.
const firmKw = (s) => (typeof s.firm_kw === "number" && Number.isFinite(s.firm_kw) ? s.firm_kw : null);

const VIEWS = {
  country: { lat: [47.2, 55.1], lon: [5.8, 15.1], label: "Germany" },
  munich: { lat: [47.95, 48.35], lon: [11.3, 11.9], label: "Munich" },
};

const canvas = document.getElementById("map-canvas");
const readout = document.getElementById("map-readout");
const sites = readPayload("sites");

if (canvas && !Array.isArray(sites)) {
  drawUnavailable(canvas, "Site payload unreadable — not an empty map");
} else if (canvas) {
  const ctx = canvas.getContext("2d");
  let view = { ...VIEWS.country };

  const project = () => {
    const [lat0, lat1] = view.lat;
    const [lon0, lon1] = view.lon;
    return (site) => ({
      x: ((site.lon - lon0) / (lon1 - lon0)) * canvas.width,
      // Latitude increases northwards, canvas y increases downwards.
      y: ((lat1 - site.lat) / (lat1 - lat0)) * canvas.height,
    });
  };

  const binFor = (kw) => BINS.find((b) => kw < b.max) || BINS[BINS.length - 1];

  function drawUnknown(x, y) {
    // Hollow ring, no fill, plus a cross — two non-colour cues, because
    // contracts/src/ui.md forbids relying on colour alone and this is the one mark a
    // judge must not confuse with a small dot.
    ctx.save();
    ctx.lineWidth = UNKNOWN_MARK.width;
    ctx.strokeStyle = UNKNOWN_MARK.stroke;
    ctx.beginPath();
    ctx.arc(x, y, UNKNOWN_MARK.r, 0, Math.PI * 2);
    ctx.stroke();
    const d = UNKNOWN_MARK.r * 0.55;
    ctx.beginPath();
    ctx.moveTo(x - d, y - d); ctx.lineTo(x + d, y + d);
    ctx.moveTo(x + d, y - d); ctx.lineTo(x - d, y + d);
    ctx.stroke();
    ctx.restore();
  }

  function drawPoints(visible, toXY) {
    for (const s of visible) {
      const { x, y } = toXY(s);
      const kw = firmKw(s);
      if (kw === null) { drawUnknown(x, y); continue; }
      const bin = binFor(kw);
      ctx.beginPath();
      ctx.arc(x, y, bin.r, 0, Math.PI * 2);
      ctx.fillStyle = bin.fill;
      ctx.fill();
      if (bin.ring) {
        ctx.lineWidth = 2.5;
        ctx.strokeStyle = "#10161c";
        ctx.stroke();
      }
    }
  }

  // Low zoom: bin to a grid of screen cells and label each with its site COUNT.
  function drawBinned(visible, toXY) {
    const CELL = 26;
    const cells = new Map();
    for (const s of visible) {
      const { x, y } = toXY(s);
      const key = `${Math.floor(x / CELL)}:${Math.floor(y / CELL)}`;
      cells.set(key, (cells.get(key) || 0) + 1);
    }
    ctx.font = "11px system-ui, sans-serif";
    ctx.textAlign = "center";
    for (const [key, count] of cells) {
      const [cx, cy] = key.split(":").map(Number);
      const x = cx * CELL + CELL / 2;
      const y = cy * CELL + CELL / 2;
      const r = Math.min(CELL / 2 - 1, 4 + Math.log2(count) * 2.5);
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = "#0b6ea8";
      ctx.globalAlpha = 0.75;
      ctx.fill();
      ctx.globalAlpha = 1;
      if (count > 1 && r > 8) {
        ctx.fillStyle = "#fff";
        ctx.fillText(String(count), x, y + 4);
      }
    }
  }

  // What the last draw() put on screen, so the hover read-out can hit-test against
  // exactly the marks a viewer can see rather than against the whole payload.
  let lastVisible = [];
  let lastToXY = null;
  let lastBinned = false;
  let hovered = null;

  // The legend promises that hovering a site shows its own EXACT firm_kw. Before this
  // handler there was no hit-testing at all, so the page promised a figure it could not
  // show. Nothing here is computed: the hovered site's own value is printed verbatim,
  // and a site whose capacity the API omitted says so instead of reading 0.
  //
  // `String(kw)` -- NOT `kw.toLocaleString()`. The argument-free form of
  // `toLocaleString()` defaults to a maximum of 3 fraction digits, so 12.34567 rendered
  // as "12.346" while this very legend promised the site's exact firm_kw (#40 item 3).
  // `String()` is JS's own exact decimal rendering of the stored double -- no rounding
  // function stands between the value on the wire and the value on screen.
  function renderReadout() {
    if (!readout) return;
    if (hovered) {
      const kw = firmKw(hovered);
      readout.textContent =
        `${hovered.site_id ?? "site"}: ` +
        (kw === null ? UNKNOWN_TEXT : `${String(kw)} kW firm capacity`);
      return;
    }
    const unknown = lastVisible.filter((s) => firmKw(s) === null).length;
    readout.textContent =
      `${lastVisible.length} sites in view` +
      (lastBinned ? " (binned: cell label = site count; hover a single site at closer zoom)" : "") +
      // Stated in words, not left to the mark alone: how many of the dots in view are an
      // absence of data rather than a low number.
      (unknown ? ` — ${unknown} with ${UNKNOWN_TEXT}` : "");
  }

  function siteAt(px, py) {
    if (lastBinned || !lastToXY) return null;
    let best = null;
    let bestD2 = 14 * 14;   // px, generous enough for the 2 px smallest mark
    for (const s of lastVisible) {
      const { x, y } = lastToXY(s);
      const d2 = (x - px) * (x - px) + (y - py) * (y - py);
      if (d2 <= bestD2) { bestD2 = d2; best = s; }
    }
    return best;
  }

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const [lat0, lat1] = view.lat;
    const [lon0, lon1] = view.lon;
    const toXY = project();
    const visible = sites.filter(
      (s) =>
        typeof s.lat === "number" &&
        typeof s.lon === "number" &&
        s.lat >= lat0 && s.lat <= lat1 && s.lon >= lon0 && s.lon <= lon1,
    );
    const binned = visible.length > 2000;
    if (binned) drawBinned(visible, toXY);
    else drawPoints(visible, toXY);
    lastVisible = visible;
    lastToXY = toXY;
    lastBinned = binned;
    if (hovered && !visible.includes(hovered)) hovered = null;
    renderReadout();
  }

  function setView(next) {
    view = { lat: [...next.lat], lon: [...next.lon] };
    draw();
  }

  document.getElementById("zoom-munich")?.addEventListener("click", () => setView(VIEWS.munich));
  document.getElementById("zoom-country")?.addEventListener("click", () => setView(VIEWS.country));

  canvas.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    const k = ev.deltaY > 0 ? 1.15 : 1 / 1.15;
    const midLat = (view.lat[0] + view.lat[1]) / 2;
    const midLon = (view.lon[0] + view.lon[1]) / 2;
    const halfLat = ((view.lat[1] - view.lat[0]) / 2) * k;
    const halfLon = ((view.lon[1] - view.lon[0]) / 2) * k;
    setView({ lat: [midLat - halfLat, midLat + halfLat], lon: [midLon - halfLon, midLon + halfLon] });
  }, { passive: false });

  // Both the hover hit-test and the drag delta compare a pointer position against
  // marks drawn in the canvas's intrinsic 1200x800 backing store, so both must convert
  // through the SAME helper (#40 item 4). `siteAt` used to compare raw
  // `ev.offsetX/offsetY` (displayed CSS pixels) against that backing store directly,
  // and the drag handler divided a raw offsetX delta by `canvas.width` -- the shared
  // responsive CSS renders the canvas at `width: 100%`, so both were displaced by the
  // same mismatched scale factor on essentially every real screen.
  let dragging = null;
  canvas.addEventListener("pointerdown", (ev) => {
    dragging = toCanvasPoint(canvas, ev.offsetX, ev.offsetY);
  });
  window.addEventListener("pointerup", () => { dragging = null; });
  canvas.addEventListener("pointerleave", () => { hovered = null; renderReadout(); });
  canvas.addEventListener("pointermove", (ev) => {
    const p = toCanvasPoint(canvas, ev.offsetX, ev.offsetY);
    if (!dragging) {
      const hit = siteAt(p.x, p.y);
      if (hit !== hovered) { hovered = hit; renderReadout(); }
      return;
    }
    const dLon = ((p.x - dragging.x) / canvas.width) * (view.lon[1] - view.lon[0]);
    const dLat = ((p.y - dragging.y) / canvas.height) * (view.lat[1] - view.lat[0]);
    setView({ lat: [view.lat[0] + dLat, view.lat[1] + dLat], lon: [view.lon[0] - dLon, view.lon[1] - dLon] });
    dragging = p;
  });

  window.addEventListener("keydown", (ev) => {
    if (ev.key === "+" || ev.key === "-") {
      canvas.dispatchEvent(new WheelEvent("wheel", { deltaY: ev.key === "+" ? -1 : 1 }));
    }
  });

  draw();
}
