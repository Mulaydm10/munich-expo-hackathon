/* Copilot — Featherless answers with local, template-grounded fallback. */
import * as api from './app-api.js';
import { el } from './app-dom.js';

const NOT_FLEXGRID = 'FlexGrid is not V2G, is not connected to real chargers, is not a live electricity-market participant, and is not TSO-certified. Charging sessions are synthesized.';

export class Copilot {
  constructor(app) {
    this.app = app;
    this.pending = null;
    this.voiceStatus = { llm: false, stt: false, tts: false };
    this.ttsOn = false;
    this.recorder = null;
    this.recorded = [];
    this.voiceStatusPromise = api.getVoiceStatus().then((status) => {
      this.voiceStatus = status;
      this.ttsOn = status.tts;
      this.updateVoiceChrome();
      return status;
    });
    this.mount();
  }

  mount() {
    const btn = el('div', { id: 'copilot-btn' },
      el('div', { className: 'mic', role: 'button', tabindex: '0', 'aria-label': 'Open copilot (text mode)' },
        el('div', { className: 'ring' })));
    this.mic = btn.querySelector('.mic');
    const bar = document.querySelector('.cmdbar');
    if (bar) { btn.classList.add('inbar'); bar.appendChild(btn); } else document.body.appendChild(btn);

    this.voiceSubtitle = el('div', { className: 'faint', text: 'Voice: text mode — speech backend not configured' });
    this.panel = el('div', { className: 'copilot' },
      el('div', { className: 'chead' },
        el('div', {},
          el('div', { text: 'Copilot' }),
          this.voiceSubtitle),
        el('div', { className: 'copilot-actions' },
          el('button', { className: 'btn ghost small', id: 'cp-tts', text: 'Speak replies' }),
          el('button', { className: 'btn ghost small', id: 'cp-close', text: 'Close' }))),
      el('div', { className: 'cbody' },
        el('div', { id: 'cp-turns', className: 'rowlist' }),
        el('div', { id: 'cp-suggest', className: 'suggest' }),
        el('div', { className: 'askrow' },
          el('input', { id: 'cp-input', placeholder: 'Ask about this scenario', 'aria-label': 'Ask the copilot' }),
          el('button', { className: 'btn small', id: 'cp-send', text: 'Ask' }))));
    document.body.appendChild(this.panel);

    btn.addEventListener('click', () => {
      if (!this.panel.classList.contains('on')) this.open();
      else this.toggleRecording();
    });
    btn.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      if (!this.panel.classList.contains('on')) this.open();
      else this.toggleRecording();
    });
    this.panel.querySelector('#cp-close').addEventListener('click', () => this.panel.classList.remove('on'));
    this.panel.querySelector('#cp-tts').addEventListener('click', () => {
      this.ttsOn = !this.ttsOn;
      this.panel.querySelector('#cp-tts').classList.toggle('off', !this.ttsOn);
    });
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

  updateVoiceChrome() {
    if (!this.mic || !this.voiceSubtitle || !this.panel) return;
    const { llm, stt } = this.voiceStatus;
    this.voiceSubtitle.textContent = llm && stt
      ? 'Voice: ElevenLabs · Answers: Featherless'
      : llm
        ? 'Answers: Featherless · Voice: text mode'
        : 'Voice: text mode — speech backend not configured';
    this.mic.setAttribute('aria-label', stt ? 'Open copilot (voice input)' : 'Open copilot (text mode)');
    this.panel.querySelector('#cp-tts').classList.toggle('off', !this.ttsOn);
  }

  async toggleRecording() {
    if (this.recorder) {
      this.recorder.stop();
      return;
    }
    await this.voiceStatusPromise;
    if (!this.voiceStatus.stt || !navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      this.turn('a', 'Voice input is not configured; type your question instead.', null, true);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.recorded = [];
      this.recorder = new MediaRecorder(stream);
      this.recorder.addEventListener('dataavailable', (event) => {
        if (event.data.size) this.recorded.push(event.data);
      });
      this.recorder.addEventListener('stop', async () => {
        stream.getTracks().forEach((track) => track.stop());
        this.mic.classList.remove('rec');
        const recorder = this.recorder;
        this.recorder = null;
        if (!recorder || !this.recorded.length) return;
        try {
          const text = await api.voiceTranscribe(new Blob(this.recorded, { type: 'audio/webm' }));
          if (!text) return;
          this.panel.querySelector('#cp-input').value = text;
          await this.send();
        } catch (error) {
          this.turn('a', `Speech-to-text unavailable: ${error.message}. Type your question instead.`, null, true);
        }
      });
      this.recorder.start();
      this.mic.classList.add('rec');
    } catch (error) {
      this.turn('a', `Microphone unavailable: ${error.message}. Type your question instead.`, null, true);
    }
  }

  turn(who, text, source, faint = false) {
    const d = el('div', { className: `turn ${who}` },
      el('span', { className: 'who', text: who === 'a' ? 'copilot' : 'you' }),
      el('span', { text }),
      source ? el('span', { className: 'faint mono', text: `source: ${source}` }) : null);
    if (faint) d.classList.add('faint');
    this.turns.appendChild(d);
    this.panel.querySelector('.cbody').scrollIntoViewIfNeeded?.();
    this.turns.parentElement.scrollTop = this.turns.parentElement.scrollHeight;
  }

  async speak(text) {
    if (!this.ttsOn || !this.voiceStatus.tts) return;
    try {
      const blob = await api.voiceSpeak(text);
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      const revoke = () => URL.revokeObjectURL(url);
      audio.addEventListener('ended', revoke, { once: true });
      audio.addEventListener('error', revoke, { once: true });
      await audio.play();
    } catch (error) {
      this.turn('a', `Text-to-speech unavailable: ${error.message}.`, null, true);
    }
  }

  async send() {
    const input = this.panel.querySelector('#cp-input');
    const q = input.value.trim();
    if (!q) return;
    input.value = '';
    this.turn('q', q, null);
    const { text, source } = await this.answer(q);
    this.turn('a', text, source);
    await this.speak(text);
  }

  async answer(qRaw) {
    const q = qRaw.toLowerCase();
    const app = this.app;
    const scn = app.scn, fa = scn.forecast_accuracy, tt = scn.totals, sc = scn.scorecard;

    if (this.pending) {
      if (/\b(yes|approve|confirm|do it|go ahead)\b/.test(q)) {
        const ev = this.pending; this.pending = null;
        const res = await app.dispatchFlow.runWith(ev);
        if (!res) return { text: 'Dispatch was not sent. Check the dispatch panel for the backend message.', source: null };
        return {
          text: `Dispatched. Worst-interval delivery ${api.fmt(res.delivered_reduction_kw_worst_interval, 'kW')} against ${api.fmt(res.promised_reduction_kw, 'kW')} promised, shortfall ${api.fmt(res.shortfall_kw, 'kW')}.`,
          source: 'dispatch.delivered_reduction_kw_worst_interval',
        };
      }
      if (/\b(no|cancel|stop)\b/.test(q)) { this.pending = null; return { text: 'Cancelled. Nothing was dispatched.', source: null }; }
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

    await this.voiceStatusPromise;
    if (this.voiceStatus.llm) {
      try {
        const response = await api.voiceAsk(qRaw, scn.id, app.selectedSite?.site_id);
        if (response) return response;
      } catch (error) {
        this.turn('a', `Featherless unavailable: ${error.message}; answering from scenario fields.`, null, true);
      }
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
        text: `Deadline misses ${api.fmt(sc.deadline_misses)}, unmet energy ${api.fmt(sc.unmet_energy_kwh, 'kWh', 1)}, envelope violations ${api.fmt(sc.envelope_violations, 'kWh', 1)}, infeasible sites ${api.fmt(sc.infeasible_sites)}. On this scenario every scheduled vehicle meets its energy and deadline.`,
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
    return { text: "I don't have that for this scenario.", source: null };
  }
}
