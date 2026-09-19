/* Network map: Germany overview → Munich zoom, clustering, site selection,
 * and a grid-stress lens on hover. Canvas 2D, no tiles, no network calls.
 * Coordinates come from the API layer (getMap/getSites). */

const C = { land: '#0b0f18', border: '#232a3a', ink: '#edeff5', dim: '#8a8f9c', faint: '#6e7585', blue: '#5a8af5', cyan: '#46c9dd', amber: '#e9a94b', lime: '#aacd4e' };

/* Coarse national outline — indicative geography for orientation only. */
const DE = [
  [9.0, 54.8], [10.0, 54.5], [11.0, 54.4], [12.3, 54.5], [13.4, 54.7], [14.2, 53.9], [14.6, 53.3],
  [14.4, 52.6], [14.7, 52.1], [14.6, 51.8], [15.0, 51.3], [14.8, 50.9], [14.3, 51.0], [13.0, 50.5],
  [12.1, 50.3], [12.4, 49.8], [13.4, 49.0], [13.8, 48.6], [12.9, 48.0], [13.0, 47.7], [12.2, 47.7],
  [11.4, 47.5], [10.5, 47.3], [10.2, 47.4], [9.6, 47.5], [8.6, 47.7], [7.7, 47.6], [7.6, 48.4],
  [8.2, 48.9], [8.1, 49.5], [6.7, 49.2], [6.4, 49.5], [6.2, 50.1], [6.0, 50.7], [5.9, 51.1],
  [6.4, 51.6], [6.7, 52.1], [7.1, 52.4], [7.2, 53.0], [8.0, 53.6], [8.5, 53.9], [8.9, 54.4], [9.0, 54.8],
];
const VIEWS = {
  de: { bbox: [5.6, 47.1, 15.3, 55.2], label: 'Germany' },
  muc: { bbox: [11.15, 47.92, 12.0, 48.38], label: 'Munich' },
};

export class NetworkMap {
  constructor(canvas, { onSelect } = {}) {
    this.canvas = canvas;
    this.view = 'de';
    this.sites = [];
    this.selected = null;
    this.hoverSite = null;
    this.onSelect = onSelect || (() => {});
    canvas.addEventListener('pointermove', (e) => this._move(e));
    canvas.addEventListener('pointerleave', () => { this.hoverSite = null; this.draw(); });
    canvas.addEventListener('click', () => {
      if (this.hoverSite) { this.selected = this.hoverSite.site_id; this.onSelect(this.hoverSite); this.draw(); }
    });
    addEventListener('resize', () => this.draw());
  }

  setData(rows) { this.sites = rows; this.draw(); }
  setView(v) { this.view = v; this.draw(); }
  select(id) { this.selected = id; this.draw(); }

  _geom() {
    const r = Math.min(devicePixelRatio || 1, 2);
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (this.canvas.width !== Math.round(w * r)) { this.canvas.width = Math.round(w * r); this.canvas.height = Math.round(h * r); }
    const ctx = this.canvas.getContext('2d');
    ctx.setTransform(r, 0, 0, r, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const [w0, s0, e0, n0] = VIEWS[this.view].bbox;
    const sx = w / (e0 - w0), sy = h / (n0 - s0);
    const s = Math.min(sx, sy) * 0.92;
    const ox = (w - (e0 - w0) * s) / 2, oy = (h - (n0 - s0) * s) / 2;
    return { ctx, w, h, p: (lon, lat) => [ox + (lon - w0) * s, h - oy - (lat - s0) * s], bbox: [w0, s0, e0, n0] };
  }

  _visible() {
    const [w0, s0, e0, n0] = VIEWS[this.view].bbox;
    return this.sites.filter((x) => x.lon >= w0 && x.lon <= e0 && x.lat >= s0 && x.lat <= n0);
  }

  _move(e) {
    const g = this._geom();
    const r = this.canvas.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    let best = null, bd = 13;
    this._visible().forEach((st) => {
      const [x, y] = g.p(st.lon, st.lat);
      const d = Math.hypot(x - mx, y - my);
      if (d < bd) { bd = d; best = st; }
    });
    this.hoverSite = best;
    this.mouse = [mx, my];
    this.draw();
  }

  draw() {
    const { ctx, w, h, p } = this._geom();
    ctx.fillStyle = '#060910'; ctx.fillRect(0, 0, w, h);

    // outline
    ctx.beginPath();
    DE.forEach(([lon, lat], i) => { const [x, y] = p(lon, lat); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.closePath();
    ctx.fillStyle = C.land; ctx.fill();
    ctx.strokeStyle = C.border; ctx.lineWidth = 1.2; ctx.stroke();

    const vis = this._visible();
    if (this.view === 'de') {
      // cluster into a coarse grid at low zoom
      const cell = 0.42, buckets = new Map();
      vis.forEach((s) => {
        const k = `${Math.round(s.lon / cell)}:${Math.round(s.lat / cell)}`;
        if (!buckets.has(k)) buckets.set(k, []);
        buckets.get(k).push(s);
      });
      // project first, then merge in SCREEN space so clusters never overprint
      let marks = [...buckets.values()].map((arr) => {
        const lon = arr.reduce((a, s) => a + s.lon, 0) / arr.length;
        const lat = arr.reduce((a, s) => a + s.lat, 0) / arr.length;
        const [x, y] = p(lon, lat);
        return { x, y, n: arr.length };
      });
      const radius = (n) => 6 + Math.min(13, Math.sqrt(n) * 2.4);
      let merged = true;
      while (merged) {
        merged = false;
        outer: for (let i = 0; i < marks.length; i++) {
          for (let k = i + 1; k < marks.length; k++) {
            const a = marks[i], b = marks[k];
            if (Math.hypot(a.x - b.x, a.y - b.y) < radius(a.n) + radius(b.n)) {
              const n = a.n + b.n;
              marks[i] = { x: (a.x * a.n + b.x * b.n) / n, y: (a.y * a.n + b.y * b.n) / n, n };
              marks.splice(k, 1);
              merged = true;
              break outer;
            }
          }
        }
      }
      marks.forEach(({ x, y, n }) => {
        const rr = radius(n);
        ctx.beginPath(); ctx.arc(x, y, rr, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(90,138,245,0.2)'; ctx.fill();
        ctx.strokeStyle = C.blue; ctx.lineWidth = 1.2; ctx.stroke();
        ctx.fillStyle = C.ink; ctx.font = '650 13px ui-sans-serif, system-ui, sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(String(n), x, y);
        if (n > 8) {
          ctx.fillStyle = C.dim; ctx.font = '12px ui-sans-serif, system-ui, sans-serif';
          ctx.textBaseline = 'top';
          ctx.fillText('sites', x, y + rr + 4);
        }
      });
    } else {
      vis.forEach((s) => {
        const [x, y] = p(s.lon, s.lat);
        const unknown = s.rated_power_kw == null;
        if (unknown) { // hollow diamond: capacity unknown, not low
          ctx.save(); ctx.translate(x, y); ctx.rotate(Math.PI / 4);
          ctx.strokeStyle = C.amber; ctx.lineWidth = 1.4; ctx.strokeRect(-3.6, -3.6, 7.2, 7.2); ctx.restore();
        } else {
          const big = s.peak_kw_baseline > 14;
          ctx.beginPath(); ctx.arc(x, y, big ? 5 : 3.2, 0, Math.PI * 2);
          ctx.fillStyle = big ? C.blue : 'rgba(90,138,245,0.48)'; ctx.fill();
        }
        if (s.site_id === this.selected) {
          ctx.beginPath(); ctx.arc(x, y, 9, 0, Math.PI * 2);
          ctx.strokeStyle = C.cyan; ctx.lineWidth = 2; ctx.stroke();
        }
      });
    }

    // view label + legend
    ctx.fillStyle = C.dim; ctx.font = '600 13px ui-sans-serif, system-ui, sans-serif';
    ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    ctx.fillText(VIEWS[this.view].label, 10, 9);
    ctx.fillStyle = C.faint; ctx.font = '12px ui-sans-serif, system-ui, sans-serif';
    ctx.fillText(this.view === 'de' ? 'clustered · click Munich to zoom' : '◇ capacity unknown   ● registered point', 10, 27);

    // grid stress lens
    if (this.hoverSite && this.mouse) this._lens(ctx, w, h);
  }

  _lens(ctx, w, h) {
    const s = this.hoverSite;
    const [mx, my] = this.mouse;
    const bw = 210, bh = 124;
    const x = Math.min(mx + 14, w - bw - 6), y = Math.min(my + 12, h - bh - 6);
    ctx.fillStyle = 'rgba(7,10,17,0.95)'; ctx.fillRect(x, y, bw, bh);
    ctx.strokeStyle = '#1d2434'; ctx.lineWidth = 1; ctx.strokeRect(x, y, bw, bh);
    const lines = [
      ['site', s.site_id],
      ['baseline peak', `${s.peak_kw_baseline} kW`],
      ['optimised peak', `${s.peak_kw_optimised} kW`],
      ['firm flexibility', `${s.firm_kw} kW`],
      ['warning', s.warnings && s.warnings.length ? s.warnings[0] : 'none'],
    ];
    ctx.font = '13px ui-sans-serif, system-ui, sans-serif'; ctx.textBaseline = 'top';
    lines.forEach(([k, v], i) => {
      ctx.fillStyle = C.faint; ctx.textAlign = 'left'; ctx.fillText(k, x + 10, y + 10 + i * 22);
      ctx.fillStyle = k === 'warning' && v !== 'none' ? C.amber : C.ink;
      ctx.textAlign = 'right'; ctx.fillText(String(v), x + bw - 10, y + 10 + i * 22);
    });
  }
}
