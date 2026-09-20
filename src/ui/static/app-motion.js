/* Landing choreography — GSAP + ScrollTrigger.
 *
 * Additive by construction. Every animation is a `gsap.from()`, never a `.to()` off a
 * hidden start state, so the page's resting CSS *is* the finished frame: if this module
 * fails to load, if GSAP is missing, or if JS is off entirely, the landing renders
 * complete and readable. That is the contracts/src/ui.md guarantee ("loads with the API
 * reachable and nothing else") applied to motion — nothing here may be load-bearing.
 *
 * Nothing in this file reads or writes a figure. Numbers belong to app-story.js, which
 * gets them from the API; motion never touches their text (a counter that ticks up from
 * zero would make "unknown" and "0" look alike, which the lane contract forbids).
 *
 * vendor/gsap.min.js and vendor/ScrollTrigger.min.js are classic scripts loaded ahead of
 * the module graph, so they arrive on window. See static/vendor/README.md.
 */

const gsap = window.gsap;
const ScrollTrigger = window.ScrollTrigger;
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

/* One easing vocabulary for the whole page. `out` is the house curve: a heavy object
   arriving and settling, never a linear slide. `inOut` is for reversible state. */
const EASE = 'cubic-bezier(0.22, 0.61, 0.24, 1)';

export function initMotion() {
  if (!gsap || !ScrollTrigger) return;          // vendored file missing → page still fine
  if (reduced) return;                          // resting CSS is already the final frame

  gsap.registerPlugin(ScrollTrigger);
  gsap.defaults({ ease: 'power3.out', duration: 0.9 });

  hero();
  panels();
  tail();
  scrollProgress();
  magnets();
  spotlights();

  // The 3D scene and the canvas sketches settle asynchronously; re-measure once they have.
  addEventListener('load', () => ScrollTrigger.refresh());
}

/* ---------- hero: one entrance, staggered by role ---------- */
function hero() {
  const q = (s) => document.querySelector(s);
  const tl = gsap.timeline({ defaults: { duration: 1.1 } });

  // Headline reveals per line out of its own overflow box (see .line-mask in app.css).
  const lines = document.querySelectorAll('.hero-copy h1 .line-mask > span');

  tl.from('.topline', { y: -18, opacity: 0, duration: 0.7 }, 0)
    .from(lines, { yPercent: 108, opacity: 0, duration: 1.15, stagger: 0.09 }, 0.1)
    .from('.hero-copy p.sub', { y: 22, opacity: 0, filter: 'blur(6px)' }, 0.45)
    .from('.hero-actions > *', { y: 16, opacity: 0, duration: 0.7, stagger: 0.07 }, 0.62)
    .from('.scrubber', { y: 26, opacity: 0, duration: 0.9 }, 0.72);

  if (q('.hero-rule')) tl.from('.hero-rule', { scaleX: 0, transformOrigin: 'left center', duration: 1.2 }, 0.3);

  // Scroll-linked exit: the hero copy lifts and dims as the sections take over. Pinned to
  // scroll position rather than time, so scrubbing back restores it exactly.
  gsap.to('.hero-copy', {
    y: -70, ease: 'none',
    scrollTrigger: { trigger: '.hero', start: 'top top', end: 'bottom 45%', scrub: 0.6 },
  });
  gsap.to('.hero-copy > :not(.hero-actions)', {
    opacity: 0.12, ease: 'none',
    scrollTrigger: { trigger: '.hero', start: 'top top', end: 'bottom 45%', scrub: 0.6 },
  });
  gsap.to('.scrubber', {
    y: 40, opacity: 0, ease: 'none',
    scrollTrigger: { trigger: '.hero', start: 'top top', end: 'bottom 70%', scrub: 0.6 },
  });
}

/* ---------- the three evidence panels ---------- */
function panels() {
  const cards = gsap.utils.toArray('.grid-3 > .panel-shell, .grid-3 > .panel');
  if (!cards.length) return;

  // Staggered arrival, each card triggered by the row so they read as one gesture.
  gsap.from(cards, {
    y: 54, opacity: 0, filter: 'blur(8px)', duration: 1.0, stagger: 0.12,
    scrollTrigger: { trigger: '.grid-3', start: 'top 82%', once: true },
  });

  // The rows inside each card land after their card does.
  cards.forEach((card) => {
    const rows = card.querySelectorAll('.kv, .panel .dim');
    if (!rows.length) return;
    gsap.from(rows, {
      y: 14, opacity: 0, duration: 0.6, stagger: 0.05,
      scrollTrigger: { trigger: card, start: 'top 74%', once: true },
    });
  });
}

/* ---------- disclosure + footer ---------- */
function tail() {
  gsap.utils.toArray('.disclosure, .sections > p.dim, footer.site').forEach((node) => {
    gsap.from(node, {
      y: 24, opacity: 0, duration: 0.8,
      scrollTrigger: { trigger: node, start: 'top 88%', once: true },
    });
  });
}

/* ---------- a hairline read-progress rule under the top edge ---------- */
function scrollProgress() {
  const bar = document.getElementById('scroll-progress');
  if (!bar) return;
  gsap.to(bar, {
    scaleX: 1, ease: 'none', transformOrigin: 'left center',
    scrollTrigger: { start: 0, end: () => document.body.scrollHeight - innerHeight, scrub: 0.3 },
  });
}

/* ---------- magnetic buttons ---------- */
/* Pointer-only: a coarse pointer gets nothing, and the transform is cleared on leave so
   focus-visible outlines and hit targets stay exactly where the layout put them. */
function magnets() {
  if (!matchMedia('(hover: hover) and (pointer: fine)').matches) return;

  document.querySelectorAll('.hero-actions .btn, footer.site a').forEach((btn) => {
    const strength = btn.classList.contains('primary') ? 0.32 : 0.22;
    const move = (e) => {
      const r = btn.getBoundingClientRect();
      gsap.to(btn, {
        x: (e.clientX - (r.left + r.width / 2)) * strength,
        y: (e.clientY - (r.top + r.height / 2)) * strength,
        duration: 0.5, ease: 'power3.out',
      });
    };
    btn.addEventListener('pointermove', move);
    btn.addEventListener('pointerleave', () => {
      gsap.to(btn, { x: 0, y: 0, duration: 0.7, ease: 'elastic.out(1, 0.5)' });
    });
  });
}

/* ---------- spotlight borders on the evidence cards ---------- */
/* Writes the pointer position into the two custom properties the .panel-shell
   ring reads. Pointer-only, and set directly rather than tweened: the ring has
   to track the cursor exactly, and an eased follow reads as lag. */
function spotlights() {
  if (!matchMedia('(hover: hover) and (pointer: fine)').matches) return;

  document.querySelectorAll('.panel-shell').forEach((card) => {
    card.addEventListener('pointermove', (e) => {
      const r = card.getBoundingClientRect();
      card.style.setProperty('--spot-x', `${((e.clientX - r.left) / r.width) * 100}%`);
      card.style.setProperty('--spot-y', `${((e.clientY - r.top) / r.height) * 100}%`);
    });
  });
}

export { EASE };
