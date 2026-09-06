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

import { readPayload, drawUnavailable } from "./payload.js";

// Display buckets, mirroring MAP_LEGEND_BINS in src/ui/api.py (the legend table is
// rendered server-side from that constant; keep the two in step). Each bucket has a
// distinct radius AND a distinct fill, so the map never relies on colour alone.
const BINS = [
  { max: 50, r: 2.0, fill: "#9fb3c8", ring: false },
  { max: 200, r: 3.5, fill: "#4a7fa5", ring: false },
  { max: 500, r: 5.0, fill: "#0b6ea8", ring: false },
  { max: Infinity, r: 7.0, fill: "#ffffff", ring: true },
];

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

  function drawPoints(visible, toXY) {
    for (const s of visible) {
      const { x, y } = toXY(s);
      const kw = typeof s.firm_kw === "number" ? s.firm_kw : 0;
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
      // A site with no firm_kw is drawn hollow, never as a smallest-bucket dot: an
      // unknown capacity must not look like a low one.
      if (typeof s.firm_kw !== "number") {
        ctx.lineWidth = 2;
        ctx.strokeStyle = "#b3261e";
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
    if (readout) {
      readout.textContent =
        `${visible.length} sites in view` + (binned ? " (binned: cell label = site count)" : "");
    }
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

  let dragging = null;
  canvas.addEventListener("pointerdown", (ev) => { dragging = { x: ev.offsetX, y: ev.offsetY }; });
  window.addEventListener("pointerup", () => { dragging = null; });
  canvas.addEventListener("pointermove", (ev) => {
    if (!dragging) return;
    const dLon = ((ev.offsetX - dragging.x) / canvas.width) * (view.lon[1] - view.lon[0]);
    const dLat = ((ev.offsetY - dragging.y) / canvas.height) * (view.lat[1] - view.lat[0]);
    setView({ lat: [view.lat[0] + dLat, view.lat[1] + dLat], lon: [view.lon[0] - dLon, view.lon[1] - dLon] });
    dragging = { x: ev.offsetX, y: ev.offsetY };
  });

  window.addEventListener("keydown", (ev) => {
    if (ev.key === "+" || ev.key === "-") {
      canvas.dispatchEvent(new WheelEvent("wheel", { deltaY: ev.key === "+" ? -1 : 1 }));
    }
  });

  draw();
}
