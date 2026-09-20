/* Control-room motion.
 *
 * Every animation here answers a question the presenter would otherwise have to
 * answer out loud: "which number just changed?", "where did that panel come
 * from?", "are we in an alert?". Motion that does not answer one of those is not
 * in this file. A control room that animates for pleasure is a control room you
 * cannot read under pressure.
 *
 * Additive, like app-motion.js: nothing is a `.to()` off a hidden start state, so
 * a missing GSAP or a JS failure leaves the screen fully rendered and usable.
 * Nothing here reads, writes, formats or animates the VALUE of a figure -- the
 * text belongs to app-simulator.js, which gets it from the API. This module only
 * draws attention to the element after the text has already changed.
 */

const gsap = window.gsap;
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

export function initControl() {
  // The skeleton state is progressive enhancement and must run even when GSAP is
  // absent or motion is reduced -- it is a correctness fix (loading must not look
  // like "no value"), not an effect. Everything below it is the effect layer.
  bootSkeletons();
  if (!gsap || reduced) return;

  flagChangedFigures();
  watchDispatchPanel();
  watchAlertState();
  spotlights();
  enterPanels();
}

/* ---------- "has this answered yet, or does it have no answer?" ---------- */

/* Every figure renders as an em-dash before the API replies, which is also how
   a figure with no value renders. For the length of the boot those two states
   were indistinguishable -- the collapse contracts/src/ui.md forbids, in the
   time dimension. Each figure now shimmers until its own text arrives, then
   marks itself loaded and resolves independently of the others.
 *
 * JS adds the class and JS removes the shimmer, so with scripting off nothing
 * shimmers and the original em-dash behaviour is untouched. */
function bootSkeletons() {
  const figures = document.querySelectorAll('.metric .v, .kv .v, .cmdbar .meta b');
  if (!figures.length) return;

  document.body.classList.add('booting');
  const figureSet = new Set(figures);
  let loaded = [...figures].filter((node) => node.hasAttribute('data-loaded')).length;
  let finished = false;
  let timer;

  const settle = (node) => {
    if (!figureSet.has(node) || node.hasAttribute('data-loaded')) return;
    node.setAttribute('data-loaded', '');
    loaded += 1;
    // An em-dash that is genuinely the API's answer keeps the `.missing`
    // treatment the base sheet already gives it. We only stop pretending it is
    // still loading.
  };

  const status = document.getElementById('m-status');
  const finish = () => {
    if (finished) return;
    finished = true;
    figures.forEach(settle);
    document.body.classList.remove('booting');
    observer.disconnect();
    clearTimeout(timer);
  };

  const observer = new MutationObserver((records) => {
    for (const r of records) {
      const node = r.target.nodeType === 3 ? r.target.parentElement : r.target;
      settle(node);
    }
    if (status?.textContent.trim().toLowerCase() === 'failed' || loaded === figures.length) finish();
  });
  figures.forEach((n) => observer.observe(n, { childList: true, characterData: true, subtree: true }));
  if (status) {
    observer.observe(status, { childList: true, characterData: true, subtree: true });
  }

  // Failsafe: a backend that never answers must not leave the screen shimmering
  // forever, because a permanent skeleton is a lie about work still happening.
  // After this the figures fall back to their honest em-dash.
  timer = setTimeout(finish, 90000);
  if (status?.textContent.trim().toLowerCase() === 'failed' || loaded === figures.length) finish();
}

/* ---------- spotlight borders ---------- */

function spotlights() {
  if (!matchMedia('(hover: hover) and (pointer: fine)').matches) return;
  document.querySelectorAll('.panel').forEach((panel) => {
    panel.addEventListener('pointermove', (e) => {
      const r = panel.getBoundingClientRect();
      panel.style.setProperty('--spot-x', `${((e.clientX - r.left) / r.width) * 100}%`);
      panel.style.setProperty('--spot-y', `${((e.clientY - r.top) / r.height) * 100}%`);
    });
  });
}

/* ---------- boot entrance ---------- */

/* The screen assembling itself in one frame reads as a page load; assembling in
   sequence reads as a system coming up, which is what it is. Bar first, then the
   centre column (the thing being demonstrated), then the rails. Short, and once. */
function enterPanels() {
  const tl = gsap.timeline({ defaults: { ease: 'power3.out' } });
  tl.from('.cmdbar', { y: -14, opacity: 0, duration: 0.45 }, 0)
    .from('.centre > .panel', { y: 16, opacity: 0, duration: 0.55, stagger: 0.08 }, 0.1)
    .from('.rail > .panel', { y: 14, opacity: 0, duration: 0.5, stagger: 0.045 }, 0.18);
}

/* ---------- "which number just changed?" ---------- */

/* A MutationObserver on the figure elements, so app-simulator.js needs no
   knowledge of this module and no call site. The flash is a background wash and
   a hairline under the value: it reads at three metres, and it fades, so a
   screen that has settled shows no highlight at all. */
function flagChangedFigures() {
  const figures = [...document.querySelectorAll(
    '.metric .v, .kv .v, .cmdbar .meta b, #site-count, #w-count, #warn-count',
  )].filter((node) => (
    node.id !== 'cursor-time' &&
    node.id !== 'm-clock' &&
    !node.closest('.scrubber, .playbar')
  ));
  if (!figures.length) return;

  const lastFlash = new WeakMap();
  const flash = (node) => {
    const now = performance.now();
    const last = lastFlash.get(node);
    if (last !== undefined && now - last < 600) return;
    lastFlash.set(node, now);
    // The element keeps its own colour; only the wash moves. Animating colour
    // on the text itself would fight the `.missing` state, which must stay
    // visually distinct from a number at all times.
    gsap.fromTo(
      node,
      { backgroundColor: 'rgba(70, 201, 221, 0.22)', boxShadow: '0 1px 0 rgba(70,201,221,0.85)' },
      {
        backgroundColor: 'rgba(70, 201, 221, 0)',
        boxShadow: '0 1px 0 rgba(70,201,221,0)',
        duration: 1.15,
        ease: 'power2.out',
        onComplete: () => gsap.set(node, { clearProps: 'backgroundColor,boxShadow' }),
      },
    );
  };

  const seen = new WeakMap();
  const observer = new MutationObserver((records) => {
    const touched = new Set();
    for (const r of records) {
      const node = r.target.nodeType === 3 ? r.target.parentElement : r.target;
      if (node) touched.add(node);
    }
    for (const node of touched) {
      const now = node.textContent;
      // First paint is not a change. Without this every figure flashes on load,
      // which trains the eye to ignore the flash exactly when it starts meaning
      // something.
      if (seen.has(node) && seen.get(node) !== now) flash(node);
      seen.set(node, now);
    }
  });

  figures.forEach((node) => {
    seen.set(node, node.textContent);
    observer.observe(node, { childList: true, characterData: true, subtree: true });
  });
}

/* ---------- "where did that panel come from?" ---------- */

/* #p-dispatch is display:none until a reduction is dispatched, then app-simulator
   fills it and shows it. Arriving without a transition makes the layout jump and
   costs the presenter a beat while judges re-find their place. */
function watchDispatchPanel() {
  const panel = document.getElementById('p-dispatch');
  if (!panel) return;

  let wasHidden = getComputedStyle(panel).display === 'none';
  const observer = new MutationObserver(() => {
    const hidden = getComputedStyle(panel).display === 'none';
    if (wasHidden && !hidden) {
      gsap.from(panel, { height: 0, opacity: 0, duration: 0.5, ease: 'power3.out', clearProps: 'height' });
      gsap.from(panel.children, { y: 10, opacity: 0, duration: 0.4, stagger: 0.04, delay: 0.12 });
    }
    wasHidden = hidden;
  });
  observer.observe(panel, { attributes: true, attributeFilter: ['style', 'class'] });
}

/* ---------- "are we in an alert?" ---------- */

/* The `.alerting` class is the loudest state this screen has. One pulse on entry
   marks the transition; it does not loop, because a looping alert becomes
   wallpaper within thirty seconds and stops being a signal at all. */
function watchAlertState() {
  const app = document.getElementById('app') || document.body;
  const bar = document.querySelector('.cmdbar');
  if (!bar) return;

  let wasAlerting = document.body.classList.contains('alerting');
  const observer = new MutationObserver(() => {
    const alerting = document.body.classList.contains('alerting') || app.classList.contains('alerting');
    if (!wasAlerting && alerting) {
      gsap.fromTo(bar, { backgroundColor: 'rgba(138, 47, 47, 0.55)' },
        { backgroundColor: 'rgba(138, 47, 47, 0)', duration: 1.1, ease: 'power2.out',
          onComplete: () => gsap.set(bar, { clearProps: 'backgroundColor' }) });
    }
    wasAlerting = alerting;
  });
  observer.observe(document.body, { attributes: true, attributeFilter: ['class'] });
  observer.observe(app, { attributes: true, attributeFilter: ['class'] });
}
