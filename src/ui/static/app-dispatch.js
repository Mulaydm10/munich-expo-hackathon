/* Dispatch: the four-field ReductionEvent and the backend response. */
import * as api from './app-api.js';
import { el } from './app-dom.js';

function field(label, name, type = 'number') {
  return el('label', { className: 'field', text: `${label} ` },
    el('input', { type, name, min: type === 'number' ? '0' : undefined, step: type === 'number' ? '5' : undefined }));
}

export class DispatchFlow {
  constructor(app) {
    this.app = app;
    this.dlg = el('dialog', { id: 'dispatch-dlg', 'aria-label': 'Dispatch reduction' },
      el('div', { className: 'dhead' },
        el('h2', { text: 'Grid operator reduction request' }),
        el('span', { className: 'tag info', text: 'POST /api/scenario/{id}/dispatch' })),
      el('div', { className: 'dbody' },
        el('div', { className: 'formgrid' },
          field('call_t (UTC ISO)', 'call_t', 'datetime-local'),
          field('notice_min', 'notice_min'),
          field('duration_min', 'duration_min'),
          field('reduction_kw', 'reduction_kw')),
        el('p', { className: 'error', id: 'derror', style: 'display:none' }),
        el('p', { className: 'sentence', id: 'dsentence' }),
        el('p', { className: 'faint', text: 'Payload contains exactly these four fields. Vehicles that would miss a departure deadline are not curtailed.' })),
      el('div', { className: 'dfoot' },
        el('button', { className: 'btn ghost small', id: 'dcancel', text: 'Cancel' }),
        el('button', { className: 'btn primary', id: 'dgo', text: 'Dispatch reduction' })));
    document.body.appendChild(this.dlg);
    this.inputs = () => Object.fromEntries([...this.dlg.querySelectorAll('input')].map((i) => [i.name, i.value]));
    this.dlg.querySelectorAll('input').forEach((i) => i.addEventListener('input', () => this.sentence()));
    this.dlg.querySelector('#dcancel').addEventListener('click', () => this.dlg.close());
    this.dlg.querySelector('#dgo').addEventListener('click', () => this.run());
    this.sentence();
  }

  event() {
    const values = this.inputs();
    const call = values.call_t ? new Date(values.call_t) : null;
    return {
      call_t: call && !Number.isNaN(call.getTime()) ? call.toISOString() : '',
      notice_min: Number(values.notice_min || 0),
      duration_min: Number(values.duration_min || 0),
      reduction_kw: Number(values.reduction_kw || 0),
    };
  }

  sentence() {
    const e = this.event();
    this.dlg.querySelector('#dsentence').textContent =
      `Reduce portfolio load by ${api.fmt(e.reduction_kw, 'kW')} in ${api.fmt(e.notice_min, 'min')} and sustain it for ${api.fmt(e.duration_min, 'min')}.`;
  }

  open(prefill = {}) {
    const values = {
      call_t: prefill.call_t || this.app.rows[this.app.cursor]?.t || '',
      notice_min: prefill.notice_min ?? 10,
      duration_min: prefill.duration_min ?? 30,
      reduction_kw: prefill.reduction_kw ?? 100,
    };
    for (const [name, value] of Object.entries(values)) {
      const input = this.dlg.querySelector(`input[name="${name}"]`);
      if (input) input.value = name === 'call_t' && value ? value.slice(0, 16) : value;
    }
    this.dlg.querySelector('#derror').style.display = 'none';
    this.sentence();
    this.dlg.showModal();
  }

  async run() {
    const event = this.event();
    const error = !event.call_t ? 'Call time is required.'
      : !Number.isFinite(event.notice_min) || event.notice_min < 0 ? 'Notice must be at least 0 minutes.'
        : !Number.isFinite(event.duration_min) || event.duration_min < 15 ? 'Duration must be at least 15 minutes.'
          : !Number.isFinite(event.reduction_kw) || event.reduction_kw <= 0 ? 'Reduction must be greater than 0 kW.'
            : '';
    if (error) {
      const node = this.dlg.querySelector('#derror');
      node.textContent = error;
      node.style.display = 'block';
      return null;
    }
    this.dlg.close();
    const app = this.app;
    app.setAlert(true);
    app.statusNote('dispatching…');
    let result;
    try {
      result = await api.dispatch(app.scn.id, event);
    } catch (e) {
      app.statusNote('dispatch failed');
      app.setAlert(false);
      const panel = document.getElementById('p-dispatch');
      panel.style.display = 'block';
      panel.replaceChildren(el('p', { className: 'warn-item', text: e.message || 'Dispatch failed.' }));
      return null;
    }
    app.applyDispatch(result, event);
    if (app.twin) {
      app.twin.shiftPulses(0.7);
      app.twin.setLoad(0.55, { phase: 'optimised' });
    }
    setTimeout(() => app.setAlert(false), 2600);
    return result;
  }

  static resultNode(res, event) {
    const worst = res.delivered_reduction_kw_worst_interval;
    const ok = res.shortfall_kw <= 0;
    const cls = ok ? 'ok' : worst > 0 ? 'warn' : 'err';
    const verdict = ok ? 'delivered' : worst > 0 ? 'partial' : 'failed';
    const root = el('div');
    root.append(
      el('header', {},
        el('h3', { text: 'Dispatch result' }),
        el('span', { className: `tag ${cls}`, text: verdict })),
      el('div', { className: 'metric' },
        el('span', { className: 'v', text: api.fmt(worst, 'kW') }),
        el('span', { className: 'k', text: 'worst-interval delivery from backend' })),
      el('div', { className: 'rowlist' },
        ...[
          ['promised reduction', api.fmt(res.promised_reduction_kw, 'kW')],
          ['delivered mean', api.fmt(res.delivered_reduction_kw_mean, 'kW')],
          ['shortfall', api.fmt(res.shortfall_kw, 'kW')],
          ['window', `${api.berlin(event.call_t)} +${event.notice_min} min for ${event.duration_min} min`],
        ].map(([key, value]) => el('div', { className: 'kv' },
          el('span', { className: 'k', text: key }), el('span', { className: 'v', text: value })))),
      ...((res.warnings || []).map((warning) => el('p', { className: 'warn-item' },
        el('span', { className: 'code', text: warning.code || 'warning' }),
        document.createTextNode(` ${warning.message || ''}`)))));
    return root;
  }
}
