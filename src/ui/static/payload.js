// Read one <script type="application/json"> payload block written by _macros.html's
// payload_json(). The block holds an API response verbatim.
//
// Rule for every module in this directory (contracts/src/ui.md, "Explicitly not this
// lane's job: computing anything"): a module may map a value to a pixel, pick a
// colour, bin points for drawing, and format a value that is already in the payload.
// It may not sum, average, fit, interpolate or otherwise derive a figure a judge
// reads as a result. If a number is not in the response, the fix is a src/service
// issue.
export function readPayload(name) {
  const el = document.getElementById(`payload-${name}`);
  if (!el) return null;
  try {
    return JSON.parse(el.textContent);
  } catch (err) {
    // Never fall through to an empty array: an unparseable payload must look broken,
    // not empty. Zero and unknown are not the same thing.
    console.error(`payload-${name} is not valid JSON`, err);
    return null;
  }
}

// Draw an explicit "this failed" message onto a canvas rather than leaving it blank.
// A blank chart reads as "the answer is zero".
export function drawUnavailable(canvas, message) {
  const ctx = canvas.getContext("2d");
  ctx.save();
  ctx.fillStyle = "#ffe3e0";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#b3261e";
  ctx.font = "bold 28px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(message, canvas.width / 2, canvas.height / 2);
  ctx.restore();
}

// Value -> pixel. Rendering, not arithmetic on a displayed figure.
export function scaler(lo, hi, pxLo, pxHi) {
  const span = hi - lo;
  if (!Number.isFinite(span) || span === 0) return () => (pxLo + pxHi) / 2;
  return (v) => pxLo + ((v - lo) / span) * (pxHi - pxLo);
}

// Extent of a field across rows, for axis limits only. Rows missing the field are
// skipped rather than treated as 0 — a gap must not drag an axis down to zero.
export function extent(rows, key) {
  let lo = Infinity;
  let hi = -Infinity;
  for (const r of rows) {
    const v = r[key];
    if (typeof v !== "number" || !Number.isFinite(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return Number.isFinite(lo) ? [lo, hi] : null;
}

// CSS-pixel pointer coordinates (a pointer event's offsetX/offsetY) -> the canvas's
// own intrinsic pixel space. A canvas's `width`/`height` attributes set the resolution
// of its backing store; the shared responsive CSS (`canvas { width: 100%; height:
// auto; }`, flexgrid.css) can render that backing store at any displayed size, and
// `offsetX`/`offsetY` are always reported in the DISPLAYED (CSS) size, not the
// intrinsic one. Comparing one space against the other, or scaling a drag delta by
// `canvas.width` instead of the on-screen width, displaces every hit-test and drag by
// the ratio between them -- which "width: 100%" makes true on essentially every real
// screen. ONE helper, used for both hover and drag, so the two paths cannot drift
// apart the way they did (#40).
export function toCanvasPoint(canvas, offsetX, offsetY) {
  const rect = canvas.getBoundingClientRect();
  const scaleX = rect.width > 0 ? canvas.width / rect.width : 1;
  const scaleY = rect.height > 0 ? canvas.height / rect.height : 1;
  return { x: offsetX * scaleX, y: offsetY * scaleY };
}

export function berlinClock(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "unknown time";
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/Berlin",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZoneName: "shortOffset",
  }).format(d);
}

// Contiguous runs of finite values for `key`, as arrays of {i, v} in row order. A gap
// is a BREAK between runs, never a bridge across one: every renderer in this directory
// draws one path per run, so a missing interval reads as missing. A filled band is the
// case that made this shared: a single polygon over all rows shades straight across a
// gap, painting envelope or sold-floor capacity the API never sent, and a shaded area
// is read as "this much was available". Zero and unknown must never look the same.
export function runs(rows, key) {
  const out = [];
  let cur = null;
  rows.forEach((r, i) => {
    const v = r ? r[key] : undefined;
    if (typeof v !== "number" || !Number.isFinite(v)) { cur = null; return; }
    if (!cur) { cur = []; out.push(cur); }
    cur.push({ i, v });
  });
  return out;
}
