# Vendored front-end libraries

`contracts/src/ui.md`: third-party front-end code is pinned by version and committed here, so the
page loads with the API reachable and nothing else. No npm, no bundler, nothing to install on a
demo laptop (`ADR-0002`, `CLAUDE.md` hard rules).

## Three.js

Version: 0.184.0

Sources:
- https://unpkg.com/three@0.184.0/build/three.module.js
- https://unpkg.com/three@0.184.0/build/three.core.js
- https://unpkg.com/three@0.184.0/examples/jsm/controls/OrbitControls.js

License: MIT (Three.js).

## GSAP + ScrollTrigger

Version: 3.15.0

Sources:
- https://unpkg.com/gsap@3.15.0/dist/gsap.min.js
- https://unpkg.com/gsap@3.15.0/dist/ScrollTrigger.min.js

Integrity (sha384, for reference if these are ever moved back to a CDN):
- gsap.min.js             sha384-XmJ9SoHtVOHoQUcKvFAzVXwdkKo1Ie3bhmSoIAkcdsHGaIrVJIkmozyq0FJeb/Ly
- ScrollTrigger.min.js    sha384-wl5TeDVvOWt30Pbf8aSo2ZrzsOjddu3avOBvHe+p+OhJt9gP6w9YXmDkN5DK2/dF

License: GreenSock Standard "no charge" licence — https://gsap.com/standard-license.
Free for this use (no sale of the tool itself). GSAP 3.13+ includes every plugin at no cost.

UMD builds, loaded with a plain classic `<script>` before the page's ES modules; they publish
`window.gsap` and `window.ScrollTrigger`, which `app-motion.js` picks up. Deliberately not the ESM
entry point: that one is a directory of ~40 files and would need a bundler to flatten.
