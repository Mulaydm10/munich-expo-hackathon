/* FlexGrid data access — the ONLY module that calls fetch().
 * All other modules receive shapes normalised from the real FastAPI routes.
 */

const DEFAULT_SPEC = { date: '2026-09-10', n_sites: 200, seed: 3 };

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: 'application/json', ...(options.headers || {}) },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = body.message || body.detail || body.error || `HTTP ${response.status}`;
    const hint = body.hint || body.how_to_fix;
    throw new Error(hint ? `${message} (${hint})` : message);
  }
  return body;
}

function warningsOf(value) {
  return Array.isArray(value) ? value : [];
}

export async function getHealth() {
  const body = await request('/api/health');
  const sha = body.git_sha || '';
  return { ok: body.status === 'ok', backend: `live · ${sha.slice(0, 7)}`, tables: body.tables || {} };
}

export async function getSites({ bbox = null, limit = 200 } = {}) {
  const query = new URLSearchParams();
  if (bbox) query.set('bbox', bbox.join(','));
  if (limit != null) query.set('limit', String(limit));
  const body = await request(`/api/sites?${query}`);
  return body.sites || [];
}

export async function createScenario(spec = DEFAULT_SPEC, onProgress) {
  let first = true;
  while (true) {
    const response = await fetch('/api/scenario', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(spec),
    });
    const body = await response.json().catch(() => ({}));
    if (response.status === 200) return { ...normalizeScenario(body), cached: first };
    if (response.status !== 202) {
      const message = body.message || body.detail || body.error || `HTTP ${response.status}`;
      const hint = body.hint || body.how_to_fix;
      throw new Error(hint ? `${message} (${hint})` : message);
    }
    onProgress?.({ progress: body.progress ?? 0, stage: body.stage || body.status });
    first = false;
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

function normalizeScenario(body) {
  const totals = body.totals || {};
  const score = body.scorecard || {};
  return {
    ...body,
    totals,
    scorecard: {
      ...score,
      unmet_energy_kwh: score.unmet_kwh ?? null,
      envelope_violations: score.envelope_violation_kwh ?? null,
      infeasible_sites: totals.infeasible_sites ?? null,
      vehicles_scheduled: totals.vehicles_scheduled ?? null,
    },
    forecast_accuracy: body.forecast_accuracy || {},
    warnings: warningsOf(body.warnings),
  };
}

export async function getScenario(spec = DEFAULT_SPEC) {
  return createScenario(spec);
}

export async function getTimeseries(id) {
  const body = await request(`/api/scenario/${encodeURIComponent(id)}/timeseries`);
  return { ...body, rows: body.rows || [], warnings: warningsOf(body.warnings) };
}

export async function getPooling(id) {
  const body = await request(`/api/scenario/${encodeURIComponent(id)}/pooling`);
  return {
    ...body,
    points: body.diversification_curve || [],
    warnings: warningsOf(body.warnings),
  };
}

export async function getMap(id) {
  const body = await request(`/api/scenario/${encodeURIComponent(id)}/map`);
  return {
    ...body,
    sites: (body.sites || []).map((site) => ({ ...site, warnings: [] })),
    warnings: warningsOf(body.warnings),
  };
}

export async function getAssumptions() {
  const body = await request('/api/assumptions');
  return Object.entries(body).map(([key, value]) => ({ key, ...(value || {}) }));
}

export async function dispatch(id, event) {
  const body = await request(`/api/scenario/${encodeURIComponent(id)}/dispatch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      call_t: event.call_t,
      notice_min: event.notice_min,
      duration_min: event.duration_min,
      reduction_kw: event.reduction_kw,
    }),
  });
  return { ...body, warnings: warningsOf(body.warnings) };
}

export function warningIsInfo(warning) {
  return String(warning.code || '').startsWith('assumption') || warning.lane === 'src/market';
}

export const MISSING = 'not available';
export function fmt(v, unit = '', digits = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return MISSING;
  const n = Number(v).toLocaleString('en-GB', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return unit ? `${n} ${unit}` : n;
}
export function berlin(tIso, opts = { hour: '2-digit', minute: '2-digit' }) {
  return new Date(tIso).toLocaleTimeString('en-GB', { timeZone: 'Europe/Berlin', ...opts });
}
export function berlinDate(tIso) {
  return new Date(tIso).toLocaleDateString('en-GB', { timeZone: 'Europe/Berlin', day: '2-digit', month: 'short', year: 'numeric' });
}
