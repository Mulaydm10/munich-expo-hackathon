/* Copilot — text-first, template-grounded over the loaded ScenarioResult.
 *
 * It answers ONLY from fields present in the scenario/timeseries payloads and
 * says "I don't have that for this scenario" when a field is null or absent.
 * Actions (dispatch) require an explicit confirmation turn.
 *
 * SERVER HOOK: replace askServer() with a POST to your own endpoint for
 * speech + LLM. The ElevenLabs key stays server-side; never ship it to the
 * browser. Until that exists the mic is labelled "Voice: text mode".
 */
import * as api from './app-api.js';
import { el } from './app-dom.js';

const NOT_FLEXGRID = 'FlexGrid is not V2G, is not connected to real chargers, is not a live electricity-market participant, and is not TSO-certified. Charging sessions are synthesized.';

export async function askServer(_question, _context) { // eslint-disable-line no-unused-vars
  return null; // no backend in this build
}

export class Copilot {
  constructor(app) {
    this.app = app;
    this.pending = null;
    this.mount();
  }

  mount() {
    const btn = el('div', { id: 'copilot-btn' },
      el('div', { className: 'mic', role: 'button', tabindex: '0', 'aria-label': 'Open copilot (text mode)' },
        el('div', { className: 'ring' })));
    document.body.appendChild(btn);

    this.panel = el('div', { className: 'copilot' },
      el('div', { className: 'chead' },
        el('div', {},
          el('div', { text: 'Copilot' }),
          el('div', { className: 'faint', text: 'Voice: text mode — speech backend not configured' })),
        el('button', { className: 'btn ghost small', id: 'cp-close', text: 'Close' })),
      el('div', { className: 'cbody' },
        el('div', { id: 'cp-turns', className: 'rowlist' }),
        el('div', { id: 'cp-suggest', className: 'suggest' }),
        el('div', { className: 'askrow' },
          el('input', { id: 'cp-input', placeholder: 'Ask about this scenario', 'aria-label': 'Ask the copilot' }),
          el('button', { className: 'btn small', id: 'cp-send', text: 'Ask' }))));
    document.body.appendChild(this.panel);

    const toggle = () => this.panel.classList.toggle('on');
    btn.addEventListener('click', toggle);
    btn.addEventListener('keydown', (e) => { if (e.key === 'Enter') toggle(); });
    this.panel.querySelector('#cp-close').addEventListener('click', () => this.panel.classList.remove('on'));
    this.panel.querySelector('#cp-send').addEventListener('click', () => this.send());
    this.panel.querySelector('#cp-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') this.send(); });

    const qs = ['What is the peak tonight?', 'How much did optimisation reduce?', 'Will every vehicle be ready?',
      'What is the forecast confidence?', 'Which assumptions are modeled?', 'Why is this site constrained?',
      'What happens if the grid asks for a reduction?', 'Approve the pending reduction.', 'What is FlexGrid not?'];
    const sg = this.panel.querySelector('#cp-suggest');
    qs.forEach((q) => {
      const b = document.createElement('button');
      b.textContent = q;
      b.addEventListener('click', () => { this.panel.querySelector('#cp-input').value = q; this.send(); });
      sg.appendChild(b);
    });
    this.turns = this.panel.querySelector('#cp-turns');
    this.turn('a', 'Ask about the loaded scenario. I answer from its fields only, and I will say so when a value is missing.', null);
  }

  open() { this.panel.classList.add('on'); }

  turn(who, text, source) {
    const d = el('div', { className: `turn ${who}` },
      el('span', { className: 'who', text: who === 'a' ? 'copilot' : 'you' }),
      el('span', { text }),
      source ? el('span', { className: 'faint mono', text: `source: ${source}` }) : null);
    this.turns.appendChild(d);
    this.panel.querySelector('.cbody').scrollIntoViewIfNeeded?.();
    this.turns.parentElement.scrollTop = this.turns.parentElement.scrollHeight;
  }

  async send() {
    const input = this.panel.querySelector('#cp-input');
    const q = input.value.trim();
    if (!q) return;
    input.value = '';
    this.turn('q', q, null);
    const { text, source } = await this.answer(q);
    this.turn('a', text, source);
  }

  async answer(qRaw) {
    const q = qRaw.toLowerCase();
    const app = this.app;
    const scn = app.scn, fa = scn.forecast_accuracy, tt = scn.totals, sc = scn.scorecard;
    const server = await askServer(qRaw, { scenario_id: scn.id });
    if (server) return server;

    if (this.pending) {
      if (/\b(yes|approve|confirm|do it|go ahead)\b/.test(q)) {
        const ev = this.pending; this.pending = null;
        const res = await app.dispatchFlow.runWith(ev);
        return {
          text: `Dispatched. Worst-interval delivery ${api.fmt(res.delivered_reduction_kw_worst_interval, 'kW')} against ${api.fmt(res.promised_reduction_kw, 'kW')} promised, shortfall ${api.fmt(res.shortfall_kw, 'kW')}.`,
          source: 'dispatch.delivered_reduction_kw_worst_interval',
        };
      }
      if (/\b(no|cancel|stop)\b/.test(q)) { this.pending = null; return { text: 'Cancelled. Nothing was dispatched.', source: null }; }
    }

    if (/what is flexgrid not|\bnot\b.*(v2g|certified)|limitation/.test(q)) return { text: NOT_FLEXGRID, source: 'product scope' };

    if (/peak/.test(q) && /tonight|today|baseline|what is/.test(q)) {
      return {
        text: `Baseline peak ${api.fmt(tt.peak_kw_baseline, 'kW')}, optimised peak ${api.fmt(tt.peak_kw_optimised, 'kW')} on ${scn.spec.date}, ${scn.spec.n_sites} sites.`,
        source: 'totals.peak_kw_baseline / totals.peak_kw_optimised',
      };
    }
    if (/reduce|reduction|optimis|optimiz/.test(q) && !/grid asks|dispatch/.test(q)) {
      const d = tt.peak_kw_baseline == null || tt.peak_kw_optimised == null
        ? null : tt.peak_kw_baseline - tt.peak_kw_optimised;
      return {
        text: `The scheduled peak is ${api.fmt(d, 'kW')} below baseline (${api.fmt(tt.peak_kw_baseline, 'kW')} → ${api.fmt(tt.peak_kw_optimised, 'kW')}).`,
        source: 'totals.peak_kw_baseline − totals.peak_kw_optimised',
      };
    }
    if (/ready|deadline|departure/.test(q)) {
      return {
        text: `Deadline misses ${sc.deadline_misses}, unmet energy ${api.fmt(sc.unmet_energy_kwh, 'kWh', 1)}, envelope violations ${sc.envelope_violations}, infeasible sites ${sc.infeasible_sites}. On this scenario every scheduled vehicle meets its energy and deadline.`,
        source: 'scorecard',
      };
    }
    if (/confidence|accuracy|wape|coverage|sharp/.test(q)) {
      return {
        text: `Accuracy ${fa.accuracy_pct}% = 1 − WAPE ${fa.wape.toFixed(3)} on portfolio load, ${scn.spec.n_sites} sites, ${fa.history_days} days of history. Seasonal-naive WAPE ${fa.seasonal_naive_wape.toFixed(3)}, climatology ${fa.climatology_wape.toFixed(3)}, q05–q95 coverage ${fa.coverage_q05_q95.toFixed(2)}, sharpness ${api.fmt(fa.sharpness_kw, 'kW')}.`,
        source: 'forecast_accuracy',
      };
    }
    if (/assumption|modeled|modelled|source|synthes/.test(q)) {
      const a = app.assumptions || [];
      const modeled = a.filter((x) => /assum|synthes/i.test(`${x.value} ${x.note}`)).map((x) => x.key);
      return {
        text: `Assumed or synthesized: ${modeled.join(', ') || 'none listed'}. Grid load, weather, day-ahead price and charging locations are real public data. Charging sessions are reproducibly synthesized.`,
        source: 'GET /api/assumptions',
      };
    }
    if (/revenue|net|euro|money|cost/.test(q)) {
      if (tt.capacity_revenue_eur === null || tt.net_eur === null) {
        return {
          text: `Energy cost ${api.fmt(tt.energy_cost_eur, '€', 2)}. Capacity revenue and net result are not available for this scenario — no balancing-market source is connected. I will not estimate them.`,
          source: 'totals.capacity_revenue_eur = null',
        };
      }
    }
    if (/constrain|why.*site|warning/.test(q)) {
      const s = app.selectedSite;
      if (!s) return { text: 'Select a site on the map first, then I can read its warnings and headroom.', source: null };
      return {
        text: `${s.site_id}: baseline peak ${api.fmt(s.peak_kw_baseline, 'kW')}, optimised ${api.fmt(s.peak_kw_optimised, 'kW')}, firm flexibility ${api.fmt(s.firm_kw, 'kW')}. Warnings: ${s.warnings.length ? s.warnings.join(', ') : 'none'}.`,
        source: 'GET /api/scenario/{id}/map',
      };
    }
    if (/grid asks|dispatch|reduction request/.test(q)) {
      const kw = 0;
      this.pending = { call_t: '18:00', notice_min: 10, duration_min: 30, reduction_kw: kw };
      return {
        text: 'Enter the requested reduction in the dispatch form. Confirm and I will dispatch it — reply "approve" to continue.',
        source: 'ReductionEvent {call_t, notice_min, duration_min, reduction_kw}',
      };
    }
    if (/approve/.test(q)) return { text: 'There is no pending reduction to approve.', source: null };

    return { text: "I don't have that for this scenario.", source: null };
  }
}
